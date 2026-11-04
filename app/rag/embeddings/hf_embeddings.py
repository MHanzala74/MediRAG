"""
HuggingFace local embedding provider.
Free, no API key — used as fallback or cost-saving option.
"""
from langchain_community.embeddings import HuggingFaceEmbeddings
from app.rag.embeddings.base import BaseEmbeddingProvider
from app.core.config import settings


class HuggingFaceEmbeddingProvider(BaseEmbeddingProvider):
    name = "huggingface"

    def get_embeddings(self) -> HuggingFaceEmbeddings:
        return HuggingFaceEmbeddings(
            model_name=settings.hf_embedding_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
