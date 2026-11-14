"""
Multi-model LLM router.
Primary: OpenAI (gpt-4o-mini)   — best quality for medical
Fallback: Groq (llama-3.3-70b)  — fast and free tier
"""
from functools import lru_cache
from langchain_core.language_models import BaseChatModel
from app.core.config import settings
from app.utils.logger import logger


def provider_name() -> str:
    """Which provider get_llm() will use (same decision rule)."""
    if settings.primary_llm == "openai" and settings.openai_api_key:
        return "openai"
    return "groq"


@lru_cache
def get_llm() -> BaseChatModel:
    if settings.primary_llm == "openai" and settings.openai_api_key:
        from langchain_openai import ChatOpenAI
        logger.info("LLM provider: OpenAI (%s)", settings.openai_model)
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0.0,          # deterministic for medical accuracy
            max_tokens=1500,
        )

    if settings.groq_api_key:
        from langchain_groq import ChatGroq
        logger.info("LLM provider: Groq (%s)", settings.groq_model)
        return ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=0.0,
            max_tokens=1500,
        )

    raise RuntimeError(
        "No LLM provider configured. Set OPENAI_API_KEY or GROQ_API_KEY in .env"
    )
