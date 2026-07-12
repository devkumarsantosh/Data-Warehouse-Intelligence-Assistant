"""
Deterministic classifier, run before any LLM call.

Most questions are structurally obvious ("which team spent the most on
EC2 last month" -> SQL; "what's our tagging policy" -> document). This
lightweight classifier scores those in milliseconds. The LLM planner
(planner.py) is only invoked when confidence is below threshold — this
is the single highest-leverage cost/latency optimization in the system,
since it keeps an LLM call off the majority of traffic.
"""
from __future__ import annotations

import re

from cdwia.common.models import ClassificationResult, QueryPath

# Lightweight lexical signal sets. In production this scoring function
# would be a small trained model (e.g. a fine-tuned sentence classifier
# or a gradient-boosted tree over TF-IDF features); regex is a readable
# stand-in with the same interface.
_SQL_SIGNALS = re.compile(
    r"\b(how much|total|sum|average|count|top \d+|spent|cost|spend|revenue|"
    r"usage|last (month|week|quarter|year)|by (team|account|service|region))\b",
    re.IGNORECASE,
)
_DOC_SIGNALS = re.compile(
    r"\b(policy|how (do|should) (i|we)|best practice|guide|documentation|"
    r"runbook|what is|explain|why)\b",
    re.IGNORECASE,
)
_HYBRID_CONNECTORS = re.compile(
    r"\b(why did|how (can|do) (i|we) (optimize|reduce|fix)|recommend|"
    r"increased|decreased|went up|went down)\b",
    re.IGNORECASE,
)


class DeterministicClassifier:
    def classify(self, text: str) -> ClassificationResult:
        sql_hit = bool(_SQL_SIGNALS.search(text))
        doc_hit = bool(_DOC_SIGNALS.search(text))
        hybrid_hit = bool(_HYBRID_CONNECTORS.search(text))

        if hybrid_hit and (sql_hit or doc_hit):
            return ClassificationResult(
                path=QueryPath.HYBRID,
                confidence=0.62,
                rationale="Both a data signal and an action/explanation signal present",
            )
        if sql_hit and not doc_hit:
            return ClassificationResult(
                path=QueryPath.SQL,
                confidence=0.9,
                rationale="Strong quantitative/aggregation signal, no doc-style signal",
            )
        if doc_hit and not sql_hit:
            return ClassificationResult(
                path=QueryPath.DOCUMENT,
                confidence=0.9,
                rationale="Strong doc/policy signal, no quantitative signal",
            )
        # Ambiguous: low confidence, let the LLM fallback decide.
        return ClassificationResult(
            path=QueryPath.HYBRID,
            confidence=0.4,
            rationale="No strong lexical signal either way",
        )
