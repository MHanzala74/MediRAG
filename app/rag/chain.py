"""
MediRAG — Production RAG Chain

Pipeline:
  NeMo Input Rail → Hybrid Retrieval + History → Context Rail
  → Cross-Encoder Rerank → Multi-Model LLM → NeMo Output Rail
  → Save to PostgreSQL
"""
from dataclasses import dataclass
from typing import AsyncIterator

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

from app.rag.vectorstore import get_vectorstore
from app.rag.retriever.hybrid_retriever import HybridRetriever
from app.rag.reranker.cross_encoder import reranker
from app.rag.llm_router import get_llm, provider_name
from app.rag.llm_usage import message_text, token_usage
from app.rag.context import build_context
from app.rag.streaming import stream_llm
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
6. Cite your sources by their document filename and page number, as shown in each [Source: …] tag.
7. Be precise, evidence-based, and compassionate.

Context documents:
{context}"""


def _build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}"),
    ])


@dataclass
class Prepared:
    """Everything needed to call the LLM — computed BEFORE any answer text exists."""
    inputs: dict | None                  # prompt variables; None if the context rail blocked the request
    sources: list[dict]
    method: str
    n_docs: int
    prompt_chars: int                    # for a token estimate if the provider reports no usage
    blocked_result: dict | None = None   # ready-made result when the context rail blocked the request


def llm_model_name() -> str:
    llm = get_llm()
    return getattr(llm, "model_name", type(llm).__name__)


async def prepare_rag(query: str, user_role: str, session_id: str) -> Prepared:
    """
    Input rail → history → hybrid retrieval → context rail → rerank → context + sources.

    Raises GuardrailException when the input rail blocks the query, so callers (including the
    streaming endpoint) can still answer with a normal HTTP error before any bytes are streamed.
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
            return Prepared(
                inputs=None, sources=[], method=method, n_docs=0, prompt_chars=0,
                blocked_result={
                    "answer":           ctx.blocked_response,
                    "sources":          [],
                    "model_used":       "none",
                    "retrieval_method": method,
                    "guardrail_passed": False,
                    "provider":         None,
                    "usage":            {"prompt_tokens": 0, "completion_tokens": 0, "tokens_estimated": False},
                },
            )

    # ── Cross-encoder reranking ───────────────────────────────────────
    final_docs = (
        reranker.rerank(query, retrieved)
        if settings.use_reranker and retrieved
        else retrieved[:settings.reranker_top_n]
    )

    # ── Build context + sources ───────────────────────────────────────
    bundle  = build_context(final_docs)
    context = bundle.text
    sources = bundle.sources

    return Prepared(
        inputs={"context": context, "chat_history": chat_history, "question": query},
        sources=sources, method=method, n_docs=len(final_docs),
        prompt_chars=len(SYSTEM_PROMPT) + len(context) + len(query) + sum(len(m.content) for m in chat_history),
    )


def _apply_output_rail(raw: str, session_id: str) -> str:
    """NeMo Layer 3: output rail + disclaimer (needs the COMPLETE answer)."""
    if not settings.enable_guardrails:
        return raw
    final_answer = nemo_guardrails.check_output(raw)
    if not nemo_guardrails.hallucination_check(raw):
        logger.warning("Hallucination signal detected | session=%s", session_id)
    return final_answer


def _result(query: str, session_id: str, prepared: Prepared, raw: str, final_answer: str, message) -> dict:
    """Persist the turn and build the result dict shared by the streaming and non-streaming paths."""
    save_message(session_id, "user",      query)
    save_message(session_id, "assistant", final_answer)
    model_name = llm_model_name()
    logger.info(
        "RAG complete | session=%s | docs=%d | model=%s | method=%s",
        session_id, prepared.n_docs, model_name, prepared.method
    )
    return {
        "answer":           final_answer,
        "sources":          prepared.sources,
        "model_used":       model_name,
        "retrieval_method": prepared.method,
        "guardrail_passed": True,
        "provider":         provider_name(),
        "usage":            token_usage(message, prompt_chars=prepared.prompt_chars, answer=raw),
    }


async def run_rag_chain(query: str, user_role: str, session_id: str) -> dict:
    """
    Full RAG pipeline with NeMo Guardrails (non-streaming: returns the finished answer).

    Returns:
        dict: answer, sources, model_used, retrieval_method, guardrail_passed, provider, usage
    """
    prepared = await prepare_rag(query, user_role, session_id)
    if prepared.blocked_result:
        return prepared.blocked_result

    # ── LLM call ──────────────────────────────────────────────────────
    chain = _build_prompt() | get_llm()
    try:
        msg = chain.invoke(prepared.inputs)
    except Exception as e:
        raise RAGException(e)
    raw = message_text(msg)
    return _result(query, session_id, prepared, raw, _apply_output_rail(raw, session_id), msg)


async def stream_rag(query: str, session_id: str, prepared: Prepared) -> AsyncIterator[dict]:
    """
    Streaming variant of the LLM step. Yields {"type": "token"|"replace", "text"} events while the
    model is generating and finally {"type": "done", "result": <same dict as run_rag_chain>}.

    Nothing is persisted unless the stream completes: if the caller stops iterating (client
    disconnected / pressed Stop) the turn is not saved to chat history.
    """
    chain = _build_prompt() | get_llm()
    end = None
    try:
        async for ev in stream_llm(chain, prepared.inputs, lambda raw: _apply_output_rail(raw, session_id)):
            if ev["type"] == "end":
                end = ev
            else:
                yield ev
    except Exception as e:
        raise RAGException(e)

    yield {"type": "done", "result": _result(query, session_id, prepared, end["raw"], end["final"], end["message"])}
