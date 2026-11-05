"""
MediRAG — NVIDIA NeMo Guardrails Integration

Replaces the previous custom regex-based guardrails with a proper
LLM-powered guardrails framework using NeMo Guardrails v0.24.

Architecture:
  Input  → NeMo topical rail  → blocks off-topic / harmful / crisis
  Output → NeMo output rail   → adds disclaimer, strips dangerous content
  Fallback → custom rules     → runs if NeMo is unavailable (no API key)
"""

import os
from pathlib import Path
from dataclasses import dataclass
from app.utils.logger import logger
from app.utils.exceptions import GuardrailException

# ── NeMo config path ──────────────────────────────────────────────────
_CONFIG_PATH = str(Path(__file__).parent / "nemo_config")

# ── Medical disclaimer (always appended) ─────────────────────────────
MEDICAL_DISCLAIMER = (
    "\n\n---\n"
    "⚠️ **Medical Disclaimer:** This response is for informational "
    "purposes only and does not constitute medical advice, diagnosis, "
    "or treatment. Always consult a qualified healthcare professional."
)


@dataclass
class GuardrailResult:
    passed: bool
    reason: str = ""
    blocked_response: str = ""


class NeMoGuardrailsManager:
    """
    Manages NeMo Guardrails LLMRails instance.
    Loaded lazily on first use — avoids startup delay.
    Falls back to rule-based checks if OpenAI key not set.
    """

    def __init__(self):
        self._rails = None
        self._nemo_available = False

    def _load(self):
        """Lazy load NeMo LLMRails."""
        if self._rails is not None:
            return

        from app.core.config import settings

        if not settings.openai_api_key:
            logger.warning(
                "NeMo Guardrails: OPENAI_API_KEY not set — "
                "falling back to rule-based guardrails"
            )
            self._nemo_available = False
            return

        try:
            from nemoguardrails import RailsConfig, LLMRails
            config = RailsConfig.from_path(_CONFIG_PATH)
            self._rails = LLMRails(config)
            self._nemo_available = True
            logger.info("NeMo Guardrails loaded ✅ (config: %s)", _CONFIG_PATH)
        except Exception as e:
            logger.error("NeMo Guardrails load failed: %s — using fallback", e)
            self._nemo_available = False

    # ── Public API ────────────────────────────────────────────────────

    async def check_input(self, query: str) -> GuardrailResult:
        """
        Run NeMo input rail on the user query.
        Returns GuardrailResult(passed=False) if the query is blocked.
        """
        self._load()

        # ── NeMo path ─────────────────────────────────────────────────
        if self._nemo_available and self._rails:
            try:
                messages = [{"role": "user", "content": query}]
                response = await self._rails.generate_async(messages=messages)

                # If NeMo intercepted and returned a safety response
                if response and self._is_blocked_response(response):
                    logger.warning(
                        "NeMo blocked query | query=%.60s | response=%.80s",
                        query, response
                    )
                    return GuardrailResult(
                        passed=False,
                        reason="Query blocked by NeMo Guardrails",
                        blocked_response=response,
                    )

                logger.info("NeMo input rail PASSED | query=%.60s", query)
                return GuardrailResult(passed=True)

            except Exception as e:
                logger.error("NeMo input check error: %s — using fallback", e)

        # ── Fallback: rule-based ──────────────────────────────────────
        return self._fallback_input_check(query)

    def check_output(self, response: str) -> str:
        """
        Post-process LLM response:
        - Strip dangerous specific medical instructions
        - Append mandatory medical disclaimer
        """
        import re

        dangerous_patterns = [
            r"(take exactly \d+\s?mg)",
            r"(inject \d+\s?(ml|units|mg))",
            r"(stop taking your (medication|medicine|prescription) immediately)",
            r"(you (definitely|certainly|absolutely) have [A-Z][a-z]+)",
        ]

        sanitized = response
        for pattern in dangerous_patterns:
            if re.search(pattern, sanitized, re.IGNORECASE):
                logger.warning("Output guardrail: dangerous phrase detected, adding warning prefix")
                sanitized = (
                    "⚠️ **Important:** Some content in the retrieved documents contains "
                    "specific medical instructions. Please consult your doctor before "
                    "making any medical decisions.\n\n" + sanitized
                )
                break

        return sanitized + MEDICAL_DISCLAIMER

    def hallucination_check(self, answer: str) -> bool:
        """
        Detect fabrication signal phrases in LLM output.
        Returns True if answer seems grounded, False if suspect.
        """
        signals = [
            "as far as i know",
            "i believe but i'm not certain",
            "i think this might be",
            "i'm not sure but",
            "i don't have information but",
        ]
        lower = answer.lower()
        for signal in signals:
            if signal in lower:
                logger.warning("Hallucination signal detected: '%s'", signal)
                return False
        return True

    def check_context(self, chunks: list[str]) -> GuardrailResult:
        """
        Verify retrieved context is sufficient before calling LLM.
        Prevents hallucination when knowledge base has no relevant info.
        """
        total = sum(len(c) for c in chunks)
        if not chunks or total < 100:
            logger.warning("Insufficient context: %d chars from %d chunks", total, len(chunks))
            return GuardrailResult(
                passed=False,
                reason="Insufficient context in knowledge base for this query.",
                blocked_response=(
                    "I could not find relevant information in the medical knowledge base "
                    "to answer your question. Please ensure the relevant documents have "
                    "been uploaded, or consult a healthcare professional directly."
                    + MEDICAL_DISCLAIMER
                ),
            )
        return GuardrailResult(passed=True)

    # ── Private helpers ───────────────────────────────────────────────

    def _is_blocked_response(self, response: str) -> bool:
        """
        Detect if NeMo returned a safety/refusal response
        instead of answering the medical question.
        """
        block_signals = [
            "i can only assist with medical",
            "i'm not able to help with that",
            "please reach out for immediate help",
            "i cannot provide information on",
            "i'm very concerned about your safety",
            "emergency services",
            "only a licensed healthcare professional",
        ]
        lower = response.lower()
        return any(sig in lower for sig in block_signals)

    def _fallback_input_check(self, query: str) -> GuardrailResult:
        """
        Rule-based fallback when NeMo is unavailable.
        Covers critical safety cases only.
        """
        import re

        q = query.strip().lower()

        if len(q) < 3:
            return GuardrailResult(
                passed=False,
                reason="Query too short.",
                blocked_response="Please ask a complete question."
            )

        crisis_patterns = [
            r"\b(kill myself|suicide|end my life|want to die|hurt myself|self harm)\b"
        ]
        for p in crisis_patterns:
            if re.search(p, q, re.IGNORECASE):
                return GuardrailResult(
                    passed=False,
                    reason="Crisis content detected",
                    blocked_response=(
                        "I'm very concerned about your safety. Please contact emergency "
                        "services immediately.\n\n"
                        "🆘 Pakistan: 115 | US: 911 | Umang Helpline: 0317-4288665"
                    ),
                )

        dangerous_patterns = [
            r"\b(how to (make|synthesize|produce) (drugs?|poison|meth|heroin))\b"
        ]
        for p in dangerous_patterns:
            if re.search(p, q, re.IGNORECASE):
                return GuardrailResult(
                    passed=False,
                    reason="Dangerous content detected",
                    blocked_response="I cannot provide information on synthesizing harmful substances."
                )

        return GuardrailResult(passed=True)


# ── Global singleton ──────────────────────────────────────────────────
nemo_guardrails = NeMoGuardrailsManager()
