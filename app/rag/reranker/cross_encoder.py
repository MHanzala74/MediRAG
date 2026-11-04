"""
Cross-encoder reranker using sentence-transformers.
Reranks retrieved docs by relevance to query before sending to LLM.

Research finding: reranking reduces LLM hallucination by ensuring
only the top-N most relevant chunks enter the prompt context window.
"""
from langchain_core.documents import Document
from app.core.config import settings
from app.utils.logger import logger


class CrossEncoderReranker:
    """
    Uses 'cross-encoder/ms-marco-MiniLM-L-6-v2' — small, fast, effective.
    Runs on CPU — no GPU required.
    """
    _model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
                logger.info("CrossEncoder reranker loaded ✅")
            except ImportError:
                logger.warning("sentence-transformers not installed — reranker disabled")
                self._model = "unavailable"

    def rerank(self, query: str, docs: list[Document], top_n: int | None = None) -> list[Document]:
        """
        Score each doc against the query and return top_n highest-scoring docs.
        """
        n = top_n or settings.reranker_top_n
        self._load()

        if self._model == "unavailable" or not docs:
            return docs[:n]

        pairs = [(query, doc.page_content) for doc in docs]
        try:
            scores = self._model.predict(pairs)
            ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
            top = [doc for _, doc in ranked[:n]]
            logger.info("Reranker: %d docs → top %d selected", len(docs), len(top))
            return top
        except Exception as e:
            logger.warning("Reranker failed, using original order: %s", e)
            return docs[:n]


reranker = CrossEncoderReranker()
