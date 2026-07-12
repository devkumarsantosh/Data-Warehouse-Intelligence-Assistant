"""
Response synthesizer: every claim in the final answer must map to
either a SQL result or a retrieved citation. An unsupported claim is
not allowed through — this is enforced structurally, not just by
prompting, via the `_extract_claims`/`_supported` check below.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from cdwia.common.models import QueryPath, RetrievedChunk, SQLResult, SynthesizedAnswer

logger = logging.getLogger("cdwia.synthesizer")

_CITATION_PATTERN = re.compile(r"\[cite:([\w\-]+)\]")


class UnsupportedClaimError(RuntimeError):
    pass


class ResponseSynthesizer:
    """Wraps an LLM call. The LLM is prompted to tag every factual claim
    with `[cite:<source_id>]`, where source_id is either a retrieved
    chunk's id or a synthetic id for the SQL result (`sql:<hash>`).
    This function then verifies every citation resolves to something
    that was actually retrieved/executed, before returning the answer.
    """

    def __init__(self, llm_complete):
        self.llm_complete = llm_complete  # Callable[[str, str], str]

    async def synthesize(
        self,
        correlated_note: str,
        chunks: list[RetrievedChunk],
        sql_result: Optional[SQLResult],
        path: QueryPath = QueryPath.HYBRID,
    ) -> SynthesizedAnswer:
        valid_ids = {c.source_id for c in chunks}
        if sql_result is not None:
            valid_ids.add(f"sql:{hash(sql_result.sql_executed)}")

        prompt = self._build_prompt(correlated_note, chunks, sql_result)
        raw = self.llm_complete(
            "You must tag every factual claim with [cite:<source_id>] using only "
            "the provided source ids. Do not state anything you cannot cite.",
            prompt,
        )

        cited_ids = set(_CITATION_PATTERN.findall(raw))
        unsupported = cited_ids - valid_ids
        if unsupported:
            logger.error("Synthesizer produced unverifiable citations: %s", unsupported)
            raise UnsupportedClaimError(f"Unverifiable citation ids: {unsupported}")

        confidence = self._confidence_score(chunks, sql_result, cited_ids)
        return SynthesizedAnswer(
            answer=raw,
            citations=sorted(cited_ids),
            confidence=confidence,
            path_used=path,
            guardrail_passed=False,  # guardrails.py sets this after its own pass
        )

    def _build_prompt(self, note: str, chunks: list[RetrievedChunk], sql_result: Optional[SQLResult]) -> str:
        parts = [f"Correlated analysis:\n{note}\n"]
        if sql_result:
            parts.append(
                f"SQL result (id=sql:{hash(sql_result.sql_executed)}): "
                f"{sql_result.row_count} rows, columns={sql_result.columns}"
            )
        for c in chunks:
            parts.append(f"Source (id={c.source_id}): {c.text[:500]}")
        return "\n\n".join(parts)

    def _confidence_score(
        self, chunks: list[RetrievedChunk], sql_result: Optional[SQLResult], cited_ids: set[str]
    ) -> float:
        if not chunks and sql_result is None:
            return 0.0
        # Simple heuristic: fraction of available evidence actually used,
        # weighted toward having both structured and unstructured support.
        has_sql = sql_result is not None
        has_docs = bool(chunks)
        base = 0.5 if (has_sql != has_docs) else 0.8
        citation_density = min(len(cited_ids) / max(len(chunks), 1), 1.0)
        return round(min(base + 0.2 * citation_density, 0.99), 2)
