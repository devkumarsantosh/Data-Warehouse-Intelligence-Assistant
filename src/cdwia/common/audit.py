"""
Immutable audit log. Required for SOC2/enterprise compliance and to
answer questions like "what did the assistant know when it gave this
recommendation on this date."

Writes are append-only; in production this targets a write-once store
(e.g. an append-only Postgres table with no UPDATE/DELETE grants, or a
dedicated log service) rather than a mutable table.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class AuditRecord:
    request_id: str
    user_id: str
    tenant_id: str
    query_text: str
    path_used: str
    sql_executed: str | None
    cited_source_ids: list[str]
    model_versions: dict[str, str]
    guardrail_passed: bool
    confidence: float
    written_at: str


class AuditSink(Protocol):
    def append(self, record: dict[str, Any]) -> None: ...


class AuditLogger:
    def __init__(self, sink: AuditSink):
        self.sink = sink

    def write(
        self,
        *,
        request_id: str,
        user_id: str,
        tenant_id: str,
        query_text: str,
        path_used: str,
        sql_executed: str | None,
        cited_source_ids: list[str],
        model_versions: dict[str, str],
        guardrail_passed: bool,
        confidence: float,
    ) -> None:
        record = AuditRecord(
            request_id=request_id,
            user_id=user_id,
            tenant_id=tenant_id,
            query_text=query_text,
            path_used=path_used,
            sql_executed=sql_executed,
            cited_source_ids=cited_source_ids,
            model_versions=model_versions,
            guardrail_passed=guardrail_passed,
            confidence=confidence,
            written_at=datetime.utcnow().isoformat() + "Z",
        )
        self.sink.append(asdict(record))


class JsonlFileAuditSink:
    """Simple local sink for dev/test; production points AuditLogger at a
    proper append-only store instead."""

    def __init__(self, path: str):
        self.path = path

    def append(self, record: dict[str, Any]) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
