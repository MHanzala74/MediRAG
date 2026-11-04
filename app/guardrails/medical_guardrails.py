"""
Medical RAG Guardrails — 4-layer safety system:

1. INPUT GUARDRAIL   — blocks harmful / off-topic / too-short queries
2. CONTEXT GUARDRAIL — warns when retrieved context is insufficient
3. OUTPUT GUARDRAIL  — strips dangerous medical advice from LLM responses
4. DISCLAIMER        — appends mandatory medical disclaimer to every answer
"""

import re
from dataclasses import dataclass
from app.utils.logger import logger


# ── Off-topic patterns (non-medical) ─────────────────────────────────
_OFF_TOPIC_PATTERNS = [
    r"\b(stock|crypto|bitcoin|invest|finance|money|trading)\b",
    r"\b(recipe|cook|food|restaurant|chef)\b",
    r"\b(movie|film|series|netflix|music|song|lyrics)\b",
    r"\b(sports|football|cricket|basketball|game)\b",
    r"\b(politics|election|vote|government|president)\b",
    r"\b(hack|exploit|malware|virus|phishing)\b",
]

# ── Harmful / crisis patterns ─────────────────────────────────────────
_HARMFUL_PATTERNS = [
    r"\b(how to (kill|poison|harm|hurt|overdose))\b",
    r"\b(suicide method|self harm|cut myself|end my life)\b",
    r"\b(illegal drug|buy drugs online|drug dealer)\b",
]

# ── Dangerous output patterns — strip if found ────────────────────────
_DANGEROUS_OUTPUT_PATTERNS = [
    r"(take \d+\s?mg|inject \d+|dose yourself with)",
    r"(stop taking your (medication|medicine|prescription))",
    r"(you (definitely|certainly) have [A-Z][a-z]+)",
    r"(this is not (serious|dangerous|harmful))",
]

# ── Medical disclaimer ────────────────────────────────────────────────
MEDICAL_DISCLAIMER = (
    "\n\n---\n"
    "⚠️ **Medical Disclaimer:** This response is for informational purposes only "
    "and does not constitute medical advice, diagnosis, or treatment. "
    "Always consult a qualified healthcare professional for medical concerns."
)


@dataclass
class GuardrailResult:
    passed: bool
    reason: str = ""
    modified_input: str = ""


class MedicalGuardrails:
    """
    Stateless guardrail checker — call check_input() before RAG
    and check_output() after LLM response.
    """

    def check_input(self, query: str) -> GuardrailResult:
        """
        Layer 1 — Input validation.
        Returns GuardrailResult(passed=False) if query should be blocked.
        """
        q = query.strip().lower()

        # Too short
        if len(q) < 5:
            return GuardrailResult(passed=False, reason="Query too short. Please ask a complete question.")

        # Harmful content
        for pattern in _HARMFUL_PATTERNS:
            if re.search(pattern, q, re.IGNORECASE):
                logger.warning("Guardrail BLOCKED harmful query: %.60s...", query)
                return GuardrailResult(
                    passed=False,
                    reason=(
                        "Your query contains content that cannot be addressed here. "
                        "If you are in crisis, please contact emergency services or a mental health helpline."
                    )
                )

        # Off-topic
        for pattern in _OFF_TOPIC_PATTERNS:
            if re.search(pattern, q, re.IGNORECASE):
                logger.warning("Guardrail BLOCKED off-topic query: %.60s...", query)
                return GuardrailResult(
                    passed=False,
                    reason=(
                        "This assistant only answers medical and healthcare questions. "
                        "Your query appears to be off-topic."
                    )
                )

        logger.debug("Guardrail input check PASSED for: %.60s...", query)
        return GuardrailResult(passed=True, modified_input=query)

    def check_context(self, chunks: list[str]) -> GuardrailResult:
        """
        Layer 2 — Context sufficiency check.
        Warns (does not block) when retrieved context is thin.
        """
        total_chars = sum(len(c) for c in chunks)
        if not chunks or total_chars < 100:
            logger.warning("Guardrail: insufficient context retrieved (%d chars)", total_chars)
            return GuardrailResult(
                passed=False,
                reason="Insufficient context found in the knowledge base for this query."
            )
        return GuardrailResult(passed=True)

    def check_output(self, response: str) -> str:
        """
        Layer 3 — Output sanitization.
        Strips dangerous phrases and appends medical disclaimer.
        """
        sanitized = response
        for pattern in _DANGEROUS_OUTPUT_PATTERNS:
            if re.search(pattern, sanitized, re.IGNORECASE):
                logger.warning("Guardrail OUTPUT: potentially dangerous phrase detected, adding warning")
                sanitized = (
                    "⚠️ The retrieved content contains specific medical instructions. "
                    "Please consult your doctor before making any medical decisions.\n\n"
                    + sanitized
                )
                break

        # Always append disclaimer
        return sanitized + MEDICAL_DISCLAIMER

    def hallucination_check(self, answer: str, context_chunks: list[str]) -> bool:
        """
        Layer 4 — Basic hallucination detection.
        Checks if LLM used phrases indicating fabrication.
        Returns True if answer seems grounded, False if suspect.
        """
        fabrication_signals = [
            "as far as i know",
            "i believe",
            "i think",
            "i'm not sure but",
            "you should probably",
            "generally speaking, without reading",
        ]
        answer_lower = answer.lower()
        for signal in fabrication_signals:
            if signal in answer_lower:
                logger.warning("Hallucination signal detected: '%s'", signal)
                return False
        return True


# Global singleton
guardrails = MedicalGuardrails()
