"""
Rough LLM cost estimates for the admin usage dashboard.

!! These are ESTIMATES. Provider prices change — verify against your provider's pricing
page and override with LLM_PRICE_INPUT_PER_1M / LLM_PRICE_OUTPUT_PER_1M in .env if needed.
"""
from app.core.config import settings

# model-name prefix -> (USD per 1M input tokens, USD per 1M output tokens)
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":   (0.15, 0.60),
    "gpt-4o":        (2.50, 10.00),
    "gpt-4.1-mini":  (0.40, 1.60),
    "gpt-4.1":       (2.00, 8.00),
    "llama-3.3-70b": (0.59, 0.79),
    "llama-3.1-8b":  (0.05, 0.08),
}


def estimate_cost(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD estimate, or None when the model's price is unknown."""
    if settings.llm_price_input_per_1m is not None and settings.llm_price_output_per_1m is not None:
        pin, pout = settings.llm_price_input_per_1m, settings.llm_price_output_per_1m
    else:
        name = (model or "").lower()
        match = max((k for k in PRICES_PER_1M if name.startswith(k)), key=len, default=None)   # longest prefix wins
        if match is None:
            return None
        pin, pout = PRICES_PER_1M[match]
    return round((prompt_tokens * pin + completion_tokens * pout) / 1_000_000, 6)
