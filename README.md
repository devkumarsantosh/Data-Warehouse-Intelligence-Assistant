# CDWIA v2 — Cloud Data Warehouse Intelligence Assistant

Reference implementation scaffold for the CDWIA v2 production
architecture: control plane / data plane split, deterministic
classifier with LLM fallback, AST-based SQL validation, hybrid
BM25 + vector retrieval, citation-enforced synthesis, guardrails,
and an async ingestion pipeline.

This is a scaffold, not a finished product: the pieces that encode the
actual design decisions (the SQL validator's decision tree, the
classifier, the hybrid orchestrator, citation enforcement) are
implemented and tested. Infra-facing edges (real OIDC verification,
real Pinecone/Postgres clients, real Kafka consumer loop) are wired
with clear interfaces and TODOs so you can drop in your actual
credentials and backends.

## Layout

```
src/cdwia/
  gateway/            API gateway — AuthN, rate limiting, WAF backstop
  control_plane/
    classifier.py     Deterministic classifier (Section 3)
    planner.py        LLM fallback planner, invoked only below confidence threshold
    policy.py         OPA policy client (RBAC/ABAC)
    cost_router.py    Model router with cost tiers + fallback chain
  data_plane/
    sql_agent/
      validator.py    AST SQL validator — the Section 6 decision tree
      executor.py     Read-only execution, timeout, circuit breaker
    knowledge_agent/
      retrieval.py    BM25 + vector search, reciprocal rank fusion, rerank
    hybrid/
      orchestrator.py Fan-out/merge flow for hybrid queries (Section 5)
  synthesizer/
    synthesizer.py    Citation-enforced answer synthesis
    guardrails.py     Outbound hallucination + PII checks
  ingestion/
    pipeline.py        Async ingestion: chunk, embed, version-tag
  common/
    models.py          Shared pydantic contracts
    config.py           Env-driven settings
    cache.py            Scope-safe result cache (Redis)
    audit.py             Immutable audit log

config/sql_allowlist.yaml   Table/column allowlist — the real SQL authorization boundary
policy/cdwia.rego            Example OPA policy (default-deny)
tests/                        Validator + classifier test suites
docker-compose.yml            Local dev stack (gateway, redis, postgres, kafka, OPA)
```

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Run tests (validator + classifier decision logic)
pytest

# Run the gateway locally
uvicorn cdwia.gateway.main:app --reload

# Or bring up the full local stack (redis, postgres, kafka, OPA)
docker compose up --build
```

## What's implemented vs stubbed

| Component | Status |
|---|---|
| AST SQL validator (5-step decision tree) | Fully implemented + tested |
| Deterministic classifier | Implemented (regex heuristic — swap in a trained classifier for production) |
| Hybrid orchestrator (fan-out/merge) | Implemented, dependency-injected for testability |
| Citation enforcement in synthesizer | Implemented |
| Guardrails (hallucination + PII scan) | Implemented (PII patterns are illustrative — use a real PII detector like Presidio) |
| Model router / fallback chain | Implemented |
| OPA policy client | Implemented, expects a real OPA server + Rego policies |
| SQL executor | Implemented, needs a real read-only DB connection factory |
| Retrieval backends (BM25 / vector / reranker) | Interfaces defined (`Protocol`), needs real backends (e.g. OpenSearch/Elastic, Pinecone, Cohere rerank) |
| Ingestion pipeline | Implemented with pluggable chunker/embedder/sinks; needs a real Kafka consumer loop wired in |
| Gateway AuthN | Placeholder — replace `_verify_token` with real OIDC/JWKS verification |

## Next steps to reach production grade

1. Wire real backends behind each `Protocol` interface (Postgres, Pinecone, OpenSearch/BM25, Kafka, OPA server, Azure AD/Okta).
2. Replace the regex classifier with a trained lightweight model; keep the same `ClassificationResult` contract.
3. Add the golden-dataset offline eval pipeline (Section 8.5 / Phase 4 of the roadmap) in CI.
4. Add OpenTelemetry tracing + Prometheus metrics around each control-plane and data-plane node.
5. Load-test the SQL cost estimator's threshold against real warehouse EXPLAIN output before setting `CDWIA_SQL_COST_THRESHOLD` in prod.

See the original design doc (`CDWIA v2 — Production Architecture Deep Dive`) for the full rationale behind each of these choices, including the tool-selection tradeoffs table (Section 8) and the phased delivery roadmap (Section 11).
