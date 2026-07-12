"""
Centralized configuration. All values are overridable via environment
variables so the same image runs in dev/staging/prod (12-factor style).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Control plane
    classifier_confidence_threshold: float = float(
        os.getenv("CDWIA_CLASSIFIER_CONFIDENCE_THRESHOLD", "0.75")
    )
    llm_planner_model: str = os.getenv("CDWIA_PLANNER_MODEL", "claude-haiku-4-5-20251001")

    # SQL layer
    sql_row_limit_default: int = int(os.getenv("CDWIA_SQL_ROW_LIMIT_DEFAULT", "10000"))
    sql_cost_threshold: float = float(os.getenv("CDWIA_SQL_COST_THRESHOLD", "5000.0"))
    sql_allowlist_path: str = os.getenv("CDWIA_SQL_ALLOWLIST_PATH", "config/sql_allowlist.yaml")
    sql_statement_timeout_ms: int = int(os.getenv("CDWIA_SQL_TIMEOUT_MS", "15000"))

    # Retrieval
    vector_top_k: int = int(os.getenv("CDWIA_VECTOR_TOP_K", "20"))
    bm25_top_k: int = int(os.getenv("CDWIA_BM25_TOP_K", "20"))
    rerank_top_k: int = int(os.getenv("CDWIA_RERANK_TOP_K", "6"))

    # Infra
    redis_url: str = os.getenv("CDWIA_REDIS_URL", "redis://localhost:6379/0")
    postgres_dsn: str = os.getenv(
        "CDWIA_POSTGRES_DSN", "postgresql://cdwia_ro:changeme@localhost:5432/warehouse"
    )
    pinecone_index: str = os.getenv("CDWIA_PINECONE_INDEX", "cdwia-docs")
    kafka_bootstrap_servers: str = os.getenv("CDWIA_KAFKA_BOOTSTRAP", "localhost:9092")
    opa_url: str = os.getenv("CDWIA_OPA_URL", "http://localhost:8181/v1/data/cdwia/allow")

    # Feature flags
    enable_llm_fallback: bool = _bool("CDWIA_ENABLE_LLM_FALLBACK", True)
    enable_pii_outbound_scan: bool = _bool("CDWIA_ENABLE_PII_OUTBOUND_SCAN", True)


settings = Settings()
