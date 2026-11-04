"""
Embedding router — selects provider based on EMBEDDING_PROVIDER env var.
Falls back to HuggingFace if OpenAI key is missing.
"""
from functools import lru_cache
from langchain_core.embeddings import Embeddings
from app.core.config import settings
from app.utils.logger import logger


@lru_cache
def get_embedding_model() -> Embeddings:
    provider = settings.embedding_provider.lower()

    if provider == "openai" and settings.openai_api_key:
        from app.rag.embeddings.openai_embeddings import OpenAIEmbeddingProvider
        logger.info("Embedding provider: OpenAI (%s)", settings.openai_embedding_model)
        return OpenAIEmbeddingProvider().get_embeddings()

    logger.info("Embedding provider: HuggingFace (%s)", settings.hf_embedding_model)
    from app.rag.embeddings.hf_embeddings import HuggingFaceEmbeddingProvider
    return HuggingFaceEmbeddingProvider().get_embeddings()
