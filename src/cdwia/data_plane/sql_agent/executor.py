"""
Executes validated, read-only SQL against the warehouse.

Defense in depth: even though the AST validator already rejects
non-SELECT statements and out-of-scope tables, the DB connection itself
uses a read-only role with row-level security, so an app-logic bug
cannot turn into a write or a cross-tenant read.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from cdwia.common.config import settings
from cdwia.common.models import SQLResult

logger = logging.getLogger("cdwia.sql_executor")


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    """Minimal circuit breaker: opens after N consecutive failures, half-opens
    after a cooldown window."""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    def _is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self.cooldown_seconds:
            # half-open: allow one trial call through
            return False
        return True

    def call(self, fn: Callable[[], SQLResult]) -> SQLResult:
        if self._is_open():
            raise CircuitOpenError("SQL executor circuit is open; failing fast")
        try:
            result = fn()
        except Exception:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = time.monotonic()
                logger.error("Circuit breaker OPEN after %d consecutive failures", self._consecutive_failures)
            raise
        else:
            self._consecutive_failures = 0
            self._opened_at = None
            return result


class SQLExecutor:
    def __init__(self, connection_factory: Callable[[], "object"], breaker: Optional[CircuitBreaker] = None):
        """
        connection_factory: returns a DB-API/psycopg-style connection bound
        to the read-only role, with row-level security session variables
        already set for the calling principal's tenant/business unit.
        """
        self.connection_factory = connection_factory
        self.breaker = breaker or CircuitBreaker()

    def execute(self, sql: str) -> SQLResult:
        def _run() -> SQLResult:
            conn = self.connection_factory()
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {settings.sql_statement_timeout_ms}")
                cur.execute(sql)
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description] if cur.description else []
            return SQLResult(
                columns=columns,
                rows=[list(r) for r in rows],
                row_count=len(rows),
                sql_executed=sql,
            )

        return self.breaker.call(_run)
