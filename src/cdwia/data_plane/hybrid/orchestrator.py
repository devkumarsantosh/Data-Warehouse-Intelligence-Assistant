"""
Hybrid query orchestrator — implements the step-by-step flow from
Section 5 of the design doc:

  1. classify (done upstream, passed in)
  2. fan-out: SQL generation and document retrieval run in parallel
  3. SQL path: generate -> AST validate -> execute (read-only)
  4. retrieval path: BM25 + vector -> RRF -> rerank/compress
  5. merge structured results + semantic context
  6. recommendation agent correlates the two
  7. synthesize with enforced citations + confidence score
  8. guardrail pass (hallucination + PII outbound scan)
  9. cache write + audit log write

This mirrors a LangGraph state machine's shape (explicit nodes and
edges, supports retries/re-planning) but is expressed here as plain
async Python so it has no framework dependency for review/testing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from cdwia.common.models import (
    IncomingQuery,
    QueryPath,
    RetrievedChunk,
    SQLResult,
    SQLValidationOutcome,
    SynthesizedAnswer,
)

logger = logging.getLogger("cdwia.hybrid_orchestrator")


@dataclass
class HybridDependencies:
    generate_sql: Callable[[str], Awaitable[str]]
    validate_sql: Callable[[str], "object"]  # returns SQLValidationResult
    execute_sql: Callable[[str], Awaitable[SQLResult]]
    retrieve_documents: Callable[[str], Awaitable[list[RetrievedChunk]]]
    correlate: Callable[[Optional[SQLResult], list[RetrievedChunk], str], Awaitable[str]]
    synthesize: Callable[[str, list[RetrievedChunk], Optional[SQLResult]], Awaitable[SynthesizedAnswer]]
    run_guardrails: Callable[[SynthesizedAnswer], Awaitable[SynthesizedAnswer]]
    cache_write: Callable[[str, SynthesizedAnswer], Awaitable[None]]
    audit_write: Callable[[IncomingQuery, SynthesizedAnswer], Awaitable[None]]
    enqueue_async_job: Callable[[str, IncomingQuery], Awaitable[None]]


async def run_hybrid_flow(query: IncomingQuery, deps: HybridDependencies) -> SynthesizedAnswer:
    # Step 3/4 fan-out: no dependency between SQL and retrieval at this
    # stage, so run them concurrently rather than sequentially.
    sql_task = asyncio.create_task(_run_sql_path(query.text, deps))
    doc_task = asyncio.create_task(deps.retrieve_documents(query.text))

    sql_result, chunks = await asyncio.gather(sql_task, doc_task)

    if sql_result == "QUEUED":
        # Cost estimator flagged this too expensive to run inline; the
        # SQL side is queued async, the doc side still returns immediately.
        await deps.enqueue_async_job(query.text, query)
        sql_result = None

    # Step 5/6: merge + correlate (not just concatenate)
    correlated_note = await deps.correlate(sql_result, chunks, query.text)

    # Step 7: synthesize with enforced citations
    answer = await deps.synthesize(correlated_note, chunks, sql_result)
    answer.path_used = QueryPath.HYBRID

    # Step 8: guardrail pass on the *outbound* text
    answer = await deps.run_guardrails(answer)

    # Step 9: cache + audit (fire concurrently, both must complete before return)
    await asyncio.gather(
        deps.cache_write(query.text, answer),
        deps.audit_write(query, answer),
    )
    return answer


async def _run_sql_path(text: str, deps: HybridDependencies):
    sql = await deps.generate_sql(text)
    validation = deps.validate_sql(sql)
    if validation.outcome == SQLValidationOutcome.EXECUTE:
        return await deps.execute_sql(validation.sql)
    if validation.outcome == SQLValidationOutcome.QUEUE_ASYNC:
        logger.info("SQL path queued async: %s", validation.reason)
        return "QUEUED"
    # Any rejection: hybrid answer proceeds on documents alone, with a
    # warning attached downstream rather than failing the whole request.
    logger.warning("SQL path rejected (%s): %s", validation.outcome, validation.reason)
    return None
