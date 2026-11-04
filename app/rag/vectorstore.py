"""
ChromaDB vectorstore singleton.
Shared across ingestion and retrieval.
"""
from functools import lru_cache
from langchain_community.vectorstores import Chroma
from app.rag.embeddings.router import get_embedding_model
from app.core.config import settings
from app.utils.logger import logger


@lru_cache
def get_vectorstore() -> Chroma:
    logger.info("Initializing ChromaDB at %s", settings.chroma_dir)
    return Chroma(
        embedding_function=get_embedding_model(),
        persist_directory=settings.chroma_dir,
    )
