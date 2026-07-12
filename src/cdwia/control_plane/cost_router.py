"""
Model router: picks a model per task by cost/quality tier and falls
back down the chain on provider error, so a single provider outage
degrades quality rather than availability.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("cdwia.cost_router")


class TaskTier(str, Enum):
    CLASSIFY = "classify"       # cheap/fast, only used as LLM fallback
    SQL_GEN = "sql_gen"         # mid-tier, precision matters
    SYNTHESIZE = "synthesize"   # highest quality needed, user-facing prose
    RERANK = "rerank"           # specialized cross-encoder, not a chat model


@dataclass(frozen=True)
class ModelChoice:
    provider: str
    model: str


# Ordered fallback chains per tier. First entry is preferred; on failure,
# the router walks the chain until one succeeds or it raises.
_ROUTING_TABLE: dict[TaskTier, list[ModelChoice]] = {
    TaskTier.CLASSIFY: [
        ModelChoice("anthropic", "claude-haiku-4-5-20251001"),
    ],
    TaskTier.SQL_GEN: [
        ModelChoice("anthropic", "claude-sonnet-5"),
        ModelChoice("anthropic", "claude-haiku-4-5-20251001"),
    ],
    TaskTier.SYNTHESIZE: [
        ModelChoice("anthropic", "claude-opus-4-8"),
        ModelChoice("anthropic", "claude-sonnet-5"),
    ],
}


class ModelRouter:
    def __init__(self, routing_table: dict[TaskTier, list[ModelChoice]] | None = None):
        self.routing_table = routing_table or _ROUTING_TABLE

    def choices_for(self, tier: TaskTier) -> list[ModelChoice]:
        return self.routing_table.get(tier, [])

    def call_with_fallback(self, tier: TaskTier, invoke_fn):
        """invoke_fn: Callable[[ModelChoice], Any]. Tries each choice in
        order, returning the first success; re-raises the last error if
        every choice in the chain fails."""
        last_exc: Exception | None = None
        for choice in self.choices_for(tier):
            try:
                return invoke_fn(choice)
            except Exception as e:  # noqa: BLE001 - deliberately broad, provider errors vary
                logger.warning("Model %s/%s failed for tier %s: %s", choice.provider, choice.model, tier, e)
                last_exc = e
                continue
        if last_exc:
            raise last_exc
        raise RuntimeError(f"No model configured for tier {tier}")
