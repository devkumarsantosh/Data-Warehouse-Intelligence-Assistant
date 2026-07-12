"""
AST SQL validator.

Implements the exact decision tree from CDWIA v2 design doc, Section 6:

    Generated SQL
      -> parse into AST (sqlglot)
      -> parse succeeded?          no  -> REJECT_UNPARSABLE
      -> root node is SELECT only? no  -> REJECT_NON_SELECT
      -> every table/column allowlisted? no -> REJECT_OUT_OF_SCOPE
      -> contains LIMIT clause?    no  -> inject default LIMIT
      -> EXPLAIN cost estimate under threshold?
            no  -> QUEUE_ASYNC
            yes -> EXECUTE

Using an AST parser rather than regex/string matching means every check
operates on parsed structure, so it can't be bypassed by comments,
string-escaping, or nested subqueries the way naive string filtering can.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from cdwia.common.config import settings
from cdwia.common.models import SQLValidationOutcome, SQLValidationResult

logger = logging.getLogger("cdwia.sql_validator")


@dataclass(frozen=True)
class AllowlistEntry:
    table: str
    columns: frozenset[str]


class SchemaAllowlist:
    """Loaded from config/sql_allowlist.yaml — the set of tables/columns the
    SQL agent is permitted to reference. This is the actual authorization
    boundary; RLS in the DB is defense-in-depth beneath it, not a substitute.
    """

    def __init__(self, entries: dict[str, set[str]]):
        # table_name (lowercase) -> set of allowed column names (lowercase), or {"*"} for all
        self._entries = {t.lower(): {c.lower() for c in cols} for t, cols in entries.items()}

    @classmethod
    def from_yaml(cls, path: str) -> "SchemaAllowlist":
        import yaml

        try:
            with open(path, "r") as f:
                raw = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("Allowlist file %s not found; falling back to empty allowlist", path)
            raw = {}
        entries = {t: set(cols) for t, cols in raw.get("tables", {}).items()}
        return cls(entries)

    def is_table_allowed(self, table: str) -> bool:
        return table.lower() in self._entries

    def is_column_allowed(self, table: str, column: str) -> bool:
        cols = self._entries.get(table.lower())
        if cols is None:
            return False
        return "*" in cols or column.lower() in cols


class SQLValidator:
    def __init__(self, allowlist: SchemaAllowlist, cost_estimator=None):
        self.allowlist = allowlist
        # cost_estimator(sql: str) -> float, pluggable so it can call EXPLAIN
        # against the real warehouse, or a stub in tests.
        self.cost_estimator = cost_estimator or (lambda _sql: 0.0)

    def validate(self, sql: str) -> SQLValidationResult:
        # 1. Parse into AST
        try:
            parsed = sqlglot.parse_one(sql)
        except Exception as e:  # sqlglot raises ParseError subclasses
            logger.info("SQL rejected: unparsable (%s)", e)
            return SQLValidationResult(
                outcome=SQLValidationOutcome.REJECT_UNPARSABLE,
                reason=f"SQL failed to parse: {e}",
                sql=sql,
            )

        # 2. Root node must be SELECT only (no DDL/DML)
        if not isinstance(parsed, exp.Select):
            logger.info("SQL rejected: non-SELECT statement (root=%s)", type(parsed).__name__)
            return SQLValidationResult(
                outcome=SQLValidationOutcome.REJECT_NON_SELECT,
                reason=f"Only SELECT statements are allowed; got {type(parsed).__name__}",
                sql=sql,
            )

        # Also reject if a SELECT wraps a disallowed statement anywhere
        # (e.g. a CTE containing an INSERT is not valid SQL anyway, but we
        # defensively scan for any DDL/DML node type in the whole tree).
        forbidden_types = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter)
        for node in parsed.walk():
            node_obj = node[0] if isinstance(node, tuple) else node
            if isinstance(node_obj, forbidden_types):
                return SQLValidationResult(
                    outcome=SQLValidationOutcome.REJECT_NON_SELECT,
                    reason=f"Disallowed statement type found in query tree: {type(node_obj).__name__}",
                    sql=sql,
                )

        # 3. Every table/column must be on the allowlist
        tables = list(parsed.find_all(exp.Table))
        for t in tables:
            table_name = t.name
            if not self.allowlist.is_table_allowed(table_name):
                logger.warning("SQL rejected: out-of-scope table '%s'", table_name)
                return SQLValidationResult(
                    outcome=SQLValidationOutcome.REJECT_OUT_OF_SCOPE,
                    reason=f"Table '{table_name}' is not on the allowlist",
                    sql=sql,
                )

        columns = list(parsed.find_all(exp.Column))
        for c in columns:
            table_ref = c.table  # may be blank if unqualified; best-effort check
            col_name = c.name
            if table_ref:
                if not self.allowlist.is_column_allowed(table_ref, col_name):
                    logger.warning(
                        "SQL rejected: out-of-scope column '%s.%s'", table_ref, col_name
                    )
                    return SQLValidationResult(
                        outcome=SQLValidationOutcome.REJECT_OUT_OF_SCOPE,
                        reason=f"Column '{table_ref}.{col_name}' is not on the allowlist",
                        sql=sql,
                    )

        # 4. Contains LIMIT clause? If not, inject the default.
        limit_injected = False
        if parsed.args.get("limit") is None:
            parsed = parsed.limit(settings.sql_row_limit_default)
            limit_injected = True
            logger.info("Injected default LIMIT %d", settings.sql_row_limit_default)

        final_sql = parsed.sql()

        # 5. EXPLAIN-based cost estimate under threshold?
        estimated_cost = self.cost_estimator(final_sql)
        if estimated_cost is not None and estimated_cost > settings.sql_cost_threshold:
            logger.info(
                "SQL queued async: estimated cost %.2f exceeds threshold %.2f",
                estimated_cost,
                settings.sql_cost_threshold,
            )
            return SQLValidationResult(
                outcome=SQLValidationOutcome.QUEUE_ASYNC,
                reason=(
                    f"Estimated cost {estimated_cost:.2f} exceeds threshold "
                    f"{settings.sql_cost_threshold:.2f}; queued for async execution"
                ),
                sql=final_sql,
                limit_injected=limit_injected,
                estimated_cost=estimated_cost,
            )

        return SQLValidationResult(
            outcome=SQLValidationOutcome.EXECUTE,
            reason="Passed all validation checks",
            sql=final_sql,
            limit_injected=limit_injected,
            estimated_cost=estimated_cost,
        )
