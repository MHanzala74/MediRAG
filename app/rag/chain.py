"""
Medical RAG Chain — production-grade with:
  - Hybrid retrieval (semantic + BM25 + RRF fusion)
  - Cross-encoder reranking
  - Chat history (multi-turn memory)
  - 4-layer guardrails
  - Multi-model LLM (OpenAI / Groq)
  - Source citation in response
"""
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from app.rag.vectorstore import get_vectorstore
from app.rag.retriever.hybrid_retriever import HybridRetriever
from app.rag.reranker.cross_encoder import reranker
from app.rag.llm_router import get_llm
from app.guardrails.medical_guardrails import guardrails
from app.db.models import save_message, get_history
from app.core.config import settings
from app.utils.logger import logger
from app.utils.exceptions import RAGException, GuardrailException


# ── System prompt ─────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a knowledgeable Medical Assistant AI.
Your role is to provide accurate, evidence-based medical information
drawn exclusively from the provided context documents.

STRICT RULES:
1. Answer ONLY from the provided context. Do NOT use outside knowledge.
2. If the context does not contain enough information, say so clearly.
3. Never diagnose a patient or prescribe specific treatments.
4. Never recommend stopping or changing medications.
5. Always recommend consulting a licensed healthcare provider.
6. Cite your sources by referencing the document filenames provided.
7. Be precise, clear, and compassionate in your responses.

Context documents:
{context}"""


def _build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}"),
    ])


async def run_rag_chain(
    query: str,
    user_role: str,
    session_id: str,
) -> dict:
    """
    Full RAG pipeline with guardrails and history.

    Returns:
        dict with answer, sources, model_used, retrieval_method, guardrail_passed
    """
    # ── Layer 1: Input Guardrail ──────────────────────────────────────
    if settings.enable_guardrails:
        g_result = guardrails.check_input(query)
        if not g_result.passed:
            logger.warning("Query blocked by guardrails: %s", g_result.reason)
            raise GuardrailException(g_result.reason)

    # ── Load chat history ─────────────────────────────────────────────
    history_rows = get_history(session_id, limit=settings.chat_history_limit)
    chat_history = []
    for row in history_rows:
        if row["role"] == "user":
            chat_history.append(HumanMessage(content=row["content"]))
        else:
            chat_history.append(AIMessage(content=row["content"]))

    # ── Hybrid retrieval ──────────────────────────────────────────────
    vectorstore = get_vectorstore()
    retriever = HybridRetriever(vectorstore)
    retrieved_docs = retriever.retrieve(query, user_role)
    retrieval_method = "hybrid" if settings.use_hybrid_search else "semantic"

    # ── Layer 2: Context Guardrail ────────────────────────────────────
    if settings.enable_guardrails:
        ctx_result = guardrails.check_context([d.page_content for d in retrieved_docs])
        if not ctx_result.passed:
            answer = (
                "I could not find relevant information in the knowledge base "
                "to answer your question. Please ensure the relevant medical "
                "documents have been uploaded, or consult a healthcare professional."
                + "\n\n---\n⚠️ **Medical Disclaimer:** Always consult a qualified healthcare professional."
            )
            save_message(session_id, "user", query)
            save_message(session_id, "assistant", answer)
            return {
                "answer": answer,
                "sources": [],
                "model_used": "none",
                "retrieval_method": retrieval_method,
                "guardrail_passed": False,
            }

    # ── Reranking ─────────────────────────────────────────────────────
    if settings.use_reranker and retrieved_docs:
        final_docs = reranker.rerank(query, retrieved_docs)
    else:
        final_docs = retrieved_docs[:settings.reranker_top_n]

    # ── Build context + sources ───────────────────────────────────────
    context_parts = []
    sources = []
    seen = set()

    for doc in final_docs:
        context_parts.append(doc.page_content)
        fname = doc.metadata.get("source", "unknown")
        doc_id = doc.metadata.get("doc_id", "")
        preview = doc.page_content[:120].replace("\n", " ")

        if fname not in seen:
            seen.add(fname)
            sources.append({
                "filename": fname,
                "doc_id": doc_id,
                "chunk_preview": preview + "...",
            })

    context = "\n\n---\n".join(context_parts)

    # ── LLM call ──────────────────────────────────────────────────────
    llm = get_llm()
    model_name = llm.model_name if hasattr(llm, "model_name") else str(type(llm).__name__)

    prompt = _build_prompt()
    chain = prompt | llm | StrOutputParser()

    try:
        raw_answer = chain.invoke({
            "context": context,
            "chat_history": chat_history,
            "question": query,
        })
    except Exception as e:
        raise RAGException(e)

    # ── Layer 3: Output Guardrail + Disclaimer ────────────────────────
    if settings.enable_guardrails:
        final_answer = guardrails.check_output(raw_answer)
        hallucination_ok = guardrails.hallucination_check(raw_answer, context_parts)
        if not hallucination_ok:
            logger.warning("Possible hallucination detected in response for session=%s", session_id)
    else:
        final_answer = raw_answer

    # ── Save to chat history ──────────────────────────────────────────
    save_message(session_id, "user",      query)
    save_message(session_id, "assistant", final_answer)

    logger.info(
        "RAG complete | session=%s | docs=%d | model=%s | method=%s",
        session_id, len(final_docs), model_name, retrieval_method
    )

    return {
        "answer":           final_answer,
        "sources":          sources,
        "model_used":       model_name,
        "retrieval_method": retrieval_method,
        "guardrail_passed": True,
    }
