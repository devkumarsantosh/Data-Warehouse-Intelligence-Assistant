"""
LLM planner — invoked only when the deterministic classifier's
confidence is below `settings.classifier_confidence_threshold`.
"""
from __future__ import annotations

import logging
from typing import Protocol

from cdwia.common.config import settings
from cdwia.common.models import ClassificationResult, QueryPath

logger = logging.getLogger("cdwia.planner")


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


_PLANNER_SYSTEM_PROMPT = """You are a query router for a data warehouse assistant.
Classify the user's question into exactly one of: sql, document, hybrid.
- sql: answerable purely from structured billing/usage data.
- document: answerable purely from policy/runbook/FinOps documentation.
- hybrid: requires both a data answer (what happened) and documentation
  context (what to do about it).
Respond with a single word: sql, document, or hybrid."""


class LLMFallbackPlanner:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def plan(self, text: str, prior: ClassificationResult) -> ClassificationResult:
        if not settings.enable_llm_fallback:
            return prior
        raw = self.llm_client.complete(_PLANNER_SYSTEM_PROMPT, text).strip().lower()
        try:
            path = QueryPath(raw)
        except ValueError:
            logger.warning("Planner returned unrecognized path '%s'; keeping classifier result", raw)
            return prior
        return ClassificationResult(
            path=path,
            confidence=max(prior.confidence, 0.85),
            used_llm_fallback=True,
            rationale=f"LLM planner override (classifier was {prior.path.value}@{prior.confidence:.2f})",
        )


def should_invoke_planner(result: ClassificationResult) -> bool:
    return result.confidence < settings.classifier_confidence_threshold
