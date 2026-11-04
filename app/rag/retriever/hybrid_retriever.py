"""
Hybrid Retriever — Semantic Search + BM25 keyword search, fused via RRF.

Research shows hybrid search outperforms pure semantic search by 10-15%
on domain-specific corpora (medical, legal, technical).

RRF (Reciprocal Rank Fusion):
  score(doc) = Σ 1 / (k + rank_i)   where k=60 (standard)
"""
from langchain_community.vectorstores import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from app.core.config import settings
from app.utils.logger import logger


def _rrf_fusion(semantic_docs: list[Document], keyword_docs: list[Document], k: int = 60) -> list[Document]:
    """Reciprocal Rank Fusion of two ranked lists."""
    scores: dict[str, float] = {}
    all_docs: dict[str, Document] = {}

    for rank, doc in enumerate(semantic_docs):
        key = doc.page_content[:100]
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        all_docs[key] = doc

    for rank, doc in enumerate(keyword_docs):
        key = doc.page_content[:100]
        scores[key] = scores.get(key, 0) + 1 / (k + rank + 1)
        all_docs[key] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [all_docs[key] for key, _ in ranked]


class HybridRetriever:
    """
    Combines ChromaDB semantic search with BM25 keyword search.
    Falls back to semantic-only if BM25 corpus is empty.
    """

    def __init__(self, vectorstore: Chroma):
        self._vectorstore = vectorstore

    def retrieve(self, query: str, user_role: str, k: int | None = None) -> list[Document]:
        n = k or settings.retriever_k

        # ── Semantic retrieval ────────────────────────────────────────
        try:
            semantic_docs = self._vectorstore.similarity_search(
                query,
                k=n,
                filter={"role": user_role},
            )
            logger.debug("Semantic retrieval: %d docs", len(semantic_docs))
        except Exception as e:
            logger.error("Semantic retrieval error: %s", e)
            semantic_docs = []

        if not settings.use_hybrid_search or not semantic_docs:
            return semantic_docs

        # ── BM25 keyword retrieval ─────────────────────────────────────
        try:
            bm25 = BM25Retriever.from_documents(semantic_docs)
            bm25.k = n
            keyword_docs = bm25.invoke(query)
            logger.debug("BM25 retrieval: %d docs", len(keyword_docs))
        except Exception as e:
            logger.warning("BM25 retrieval failed, using semantic only: %s", e)
            return semantic_docs

        # ── RRF Fusion ────────────────────────────────────────────────
        fused = _rrf_fusion(semantic_docs, keyword_docs)
        logger.info("Hybrid RRF fusion: %d docs → %d unique", len(semantic_docs) + len(keyword_docs), len(fused))
        return fused
