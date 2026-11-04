"""OpenAI text-embedding-3-small provider."""
from langchain_openai import OpenAIEmbeddings
from app.rag.embeddings.base import BaseEmbeddingProvider
from app.core.config import settings


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    name = "openai"

    def get_embeddings(self) -> OpenAIEmbeddings:
        return OpenAIEmbeddings(
            model=settings.openai_embedding_model,
            api_key=settings.openai_api_key or None,
        )
