import pytest

from cdwia.common.models import SQLValidationOutcome
from cdwia.data_plane.sql_agent.validator import SchemaAllowlist, SQLValidator


@pytest.fixture
def allowlist():
    return SchemaAllowlist(
        {
            "billing_line_items": {"account_id", "service_code", "cost_usd", "usage_date"},
            "storage_usage_daily": {"*"},
        }
    )


@pytest.fixture
def validator(allowlist):
    return SQLValidator(allowlist=allowlist, cost_estimator=lambda sql: 10.0)


def test_unparsable_sql_is_rejected(validator):
    result = validator.validate("SELECT * FROM (((")
    assert result.outcome == SQLValidationOutcome.REJECT_UNPARSABLE


def test_non_select_statement_is_rejected(validator):
    result = validator.validate("DELETE FROM billing_line_items WHERE account_id = '123'")
    assert result.outcome == SQLValidationOutcome.REJECT_NON_SELECT


def test_ddl_hidden_in_cte_is_rejected(validator):
    # Defensive check: even if wrapped, non-SELECT node types are caught.
    result = validator.validate(
        "WITH x AS (SELECT 1) INSERT INTO billing_line_items VALUES (1)"
    )
    assert result.outcome == SQLValidationOutcome.REJECT_NON_SELECT


def test_out_of_scope_table_is_rejected(validator):
    result = validator.validate("SELECT * FROM secret_salaries")
    assert result.outcome == SQLValidationOutcome.REJECT_OUT_OF_SCOPE


def test_out_of_scope_column_is_rejected(validator):
    result = validator.validate(
        "SELECT billing_line_items.ssn FROM billing_line_items"
    )
    assert result.outcome == SQLValidationOutcome.REJECT_OUT_OF_SCOPE


def test_missing_limit_is_injected_not_rejected(validator):
    result = validator.validate(
        "SELECT account_id, cost_usd FROM billing_line_items WHERE usage_date = '2026-07-01'"
    )
    assert result.outcome == SQLValidationOutcome.EXECUTE
    assert result.limit_injected is True
    assert "LIMIT" in result.sql.upper()


def test_existing_limit_is_preserved(validator):
    result = validator.validate(
        "SELECT account_id FROM billing_line_items LIMIT 50"
    )
    assert result.outcome == SQLValidationOutcome.EXECUTE
    assert result.limit_injected is False


def test_expensive_query_is_queued_not_executed(allowlist):
    validator = SQLValidator(allowlist=allowlist, cost_estimator=lambda sql: 999999.0)
    result = validator.validate("SELECT * FROM storage_usage_daily")
    assert result.outcome == SQLValidationOutcome.QUEUE_ASYNC
    assert result.estimated_cost == 999999.0


def test_comment_based_bypass_attempt_still_rejected(validator):
    # A regex/string filter might be fooled by comments; the AST parser
    # sees through them to the real statement type.
    result = validator.validate(
        "SELECT * FROM billing_line_items; -- ' OR 1=1 -- \nDROP TABLE billing_line_items;"
    )
    # sqlglot's default dialect parses this as multiple statements /
    # fails to parse as one SELECT; either unparsable or non-select is
    # an acceptable rejection outcome here — the key invariant is that
    # it must NOT be EXECUTE.
    assert result.outcome != SQLValidationOutcome.EXECUTE
