"""
Shared data contracts between the control plane and data plane.

Keeping these in one module means a policy change or a new field
(e.g. a data-scope tag) is defined once and consumed everywhere,
rather than re-declared per-service.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryPath(str, Enum):
    SQL = "sql"
    DOCUMENT = "document"
    HYBRID = "hybrid"


class Principal(BaseModel):
    """Authenticated caller, as resolved by the API gateway (OIDC token claims)."""

    user_id: str
    tenant_id: str
    business_unit: str
    roles: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)  # for ABAC


class IncomingQuery(BaseModel):
    request_id: str
    principal: Principal
    text: str
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class ClassificationResult(BaseModel):
    path: QueryPath
    confidence: float
    used_llm_fallback: bool = False
    rationale: Optional[str] = None


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str
    denied_scopes: list[str] = Field(default_factory=list)


class SQLValidationOutcome(str, Enum):
    REJECT_UNPARSABLE = "reject_unparsable"
    REJECT_NON_SELECT = "reject_non_select"
    REJECT_OUT_OF_SCOPE = "reject_out_of_scope"
    QUEUE_ASYNC = "queue_async"
    EXECUTE = "execute"


class SQLValidationResult(BaseModel):
    outcome: SQLValidationOutcome
    reason: str
    sql: str
    limit_injected: bool = False
    estimated_cost: Optional[float] = None


class RetrievedChunk(BaseModel):
    source_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SQLResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    sql_executed: str


class SynthesizedAnswer(BaseModel):
    answer: str
    citations: list[str]
    confidence: float
    path_used: QueryPath
    guardrail_passed: bool
    warnings: list[str] = Field(default_factory=list)
