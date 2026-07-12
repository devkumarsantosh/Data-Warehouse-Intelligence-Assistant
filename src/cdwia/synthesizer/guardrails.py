"""
Guardrail pass, run on the *outbound* text after synthesis — distinct
from inbound prompt-injection scanning in the gateway. Two checks:

  1. Hallucination re-check: an independent pass (cheap model or
     rule-based) re-verifies each cited claim actually matches its
     source text, catching cases where the synthesizer cited correctly
     but paraphrased inaccurately.
  2. PII outbound scan: catches cases where a SQL join surfaces a
     column a role shouldn't see, even though the semantic layer is
     supposed to exclude PII-bearing columns entirely (defense in depth).
"""
from __future__ import annotations

import logging
import re

from cdwia.common.models import SynthesizedAnswer

logger = logging.getLogger("cdwia.guardrails")

# Illustrative patterns only — production would use a dedicated PII
# detection model/service (e.g. Presidio) rather than regex alone.
_PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "ssn_like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card_like": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}


class HallucinationChecker:
    def __init__(self, llm_complete):
        self.llm_complete = llm_complete

    def check(self, answer_text: str, source_texts: list[str]) -> tuple[bool, str]:
        if not source_texts:
            return True, "no sources to verify against"
        prompt = (
            "Answer:\n" + answer_text + "\n\nSources:\n" + "\n---\n".join(source_texts) +
            "\n\nDoes every factual claim in the answer follow from the sources? "
            "Reply PASS or FAIL followed by a one-sentence reason."
        )
        verdict = self.llm_complete(
            "You are a strict fact-checker comparing an answer against its sources.", prompt
        )
        passed = verdict.strip().upper().startswith("PASS")
        return passed, verdict


def scan_outbound_pii(text: str) -> list[str]:
    findings = []
    for label, pattern in _PII_PATTERNS.items():
        if pattern.search(text):
            findings.append(label)
    return findings


def run_guardrails(
    answer: SynthesizedAnswer, source_texts: list[str], hallucination_checker: HallucinationChecker
) -> SynthesizedAnswer:
    warnings = list(answer.warnings)

    passed, verdict = hallucination_checker.check(answer.answer, source_texts)
    if not passed:
        logger.error("Hallucination check FAILED: %s", verdict)
        warnings.append(f"hallucination_check_failed: {verdict}")

    pii_hits = scan_outbound_pii(answer.answer)
    if pii_hits:
        logger.error("Outbound PII scan found: %s", pii_hits)
        warnings.append(f"pii_detected: {pii_hits}")
        # Redact rather than silently ship PII to the user.
        redacted = answer.answer
        for label, pattern in _PII_PATTERNS.items():
            if label in pii_hits:
                redacted = pattern.sub("[REDACTED]", redacted)
        answer = answer.model_copy(update={"answer": redacted})

    guardrail_passed = passed and not pii_hits
    return answer.model_copy(update={"guardrail_passed": guardrail_passed, "warnings": warnings})
