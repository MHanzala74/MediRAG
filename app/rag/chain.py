"""
MediRAG — Production RAG Chain

Pipeline:
  NeMo Input Rail → Hybrid Retrieval + History → Context Rail
  → Cross-Encoder Rerank → Multi-Model LLM → NeMo Output Rail
  → Save to PostgreSQL
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.rag.vectorstore import get_vectorstore
from app.rag.retriever.hybrid_retriever import HybridRetriever
from app.rag.reranker.cross_encoder import reranker
from app.rag.llm_router import get_llm
from app.guardrails.nemo_guardrails import nemo_guardrails
from app.db.models import save_message, get_history
from app.core.config import settings
from app.utils.logger import logger
from app.utils.exceptions import RAGException, GuardrailException


SYSTEM_PROMPT = """You are MediRAG, a knowledgeable Medical Assistant AI.
Answer ONLY from the provided context documents.

STRICT RULES:
1. Answer exclusively from the context below — no outside knowledge.
2. If the context lacks sufficient information, say so clearly.
3. Never diagnose patients or prescribe specific treatments.
4. Never recommend stopping or changing prescribed medications.
5. Always recommend consulting a licensed healthcare professional.
6. Cite your sources by referencing the document filenames.
7. Be precise, evidence-based, and compassionate.

Context documents:
{context}"""


def _build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}"),
    ])


async def run_rag_chain(query: str, user_role: str, session_id: str) -> dict:
    """
    Full RAG pipeline with NeMo Guardrails.

    Returns:
        dict: answer, sources, model_used, retrieval_method, guardrail_passed
    """

    # ── NeMo Layer 1: Input Rail ──────────────────────────────────────
    if settings.enable_guardrails:
        g = await nemo_guardrails.check_input(query)
        if not g.passed:
            logger.warning("NeMo input rail blocked | session=%s | reason=%s", session_id, g.reason)
            raise GuardrailException(g.blocked_response or g.reason)

    # ── Load chat history ─────────────────────────────────────────────
    history_rows = get_history(session_id, limit=settings.chat_history_limit)
    chat_history = [
        HumanMessage(content=r["content"]) if r["role"] == "user"
        else AIMessage(content=r["content"])
        for r in history_rows
    ]

    # ── Hybrid retrieval ──────────────────────────────────────────────
    vectorstore  = get_vectorstore()
    retriever    = HybridRetriever(vectorstore)
    retrieved    = retriever.retrieve(query, user_role)
    method       = "hybrid" if settings.use_hybrid_search else "semantic"

    # ── NeMo Layer 2: Context Rail ────────────────────────────────────
    if settings.enable_guardrails:
        ctx = nemo_guardrails.check_context([d.page_content for d in retrieved])
        if not ctx.passed:
            save_message(session_id, "user",      query)
            save_message(session_id, "assistant", ctx.blocked_response)
            return {
                "answer":           ctx.blocked_response,
                "sources":          [],
                "model_used":       "none",
                "retrieval_method": method,
                "guardrail_passed": False,
            }

    # ── Cross-encoder reranking ───────────────────────────────────────
    final_docs = (
        reranker.rerank(query, retrieved)
        if settings.use_reranker and retrieved
        else retrieved[:settings.reranker_top_n]
    )

    # ── Build context + sources ───────────────────────────────────────
    context_parts, sources, seen = [], [], set()
    for doc in final_docs:
        context_parts.append(doc.page_content)
        fname   = doc.metadata.get("source",  "unknown")
        doc_id  = doc.metadata.get("doc_id",  "")
        preview = doc.page_content[:120].replace("\n", " ")
        if fname not in seen:
            seen.add(fname)
            sources.append({
                "filename":      fname,
                "doc_id":        doc_id,
                "chunk_preview": preview + "...",
            })

    context = "\n\n---\n".join(context_parts)

    # ── LLM call ──────────────────────────────────────────────────────
    llm        = get_llm()
    model_name = getattr(llm, "model_name", type(llm).__name__)
    chain      = _build_prompt() | llm | StrOutputParser()

    try:
        raw = chain.invoke({
            "context":      context,
            "chat_history": chat_history,
            "question":     query,
        })
    except Exception as e:
        raise RAGException(e)

    # ── NeMo Layer 3: Output Rail + Disclaimer ────────────────────────
    if settings.enable_guardrails:
        final_answer = nemo_guardrails.check_output(raw)
        if not nemo_guardrails.hallucination_check(raw):
            logger.warning("Hallucination signal detected | session=%s", session_id)
    else:
        final_answer = raw

    # ── Persist to PostgreSQL ─────────────────────────────────────────
    save_message(session_id, "user",      query)
    save_message(session_id, "assistant", final_answer)

    logger.info(
        "RAG complete | session=%s | docs=%d | model=%s | method=%s",
        session_id, len(final_docs), model_name, method
    )

    return {
        "answer":           final_answer,
        "sources":          sources,
        "model_used":       model_name,
        "retrieval_method": method,
        "guardrail_passed": True,
    }
