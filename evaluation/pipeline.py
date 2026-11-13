"""
Side-effect-free RAG runner for evaluation.

`app.rag.chain.run_rag_chain` returns only the answer + source previews and
writes every turn to PostgreSQL. DeepEval additionally needs the *retrieval
context* (the exact chunks the LLM saw), so this module re-uses the production
building blocks — hybrid retriever, cross-encoder reranker, prompt, LLM router
and NeMo guardrails — but:

  * returns the retrieval context,
  * never touches PostgreSQL / chat history,
  * measures latency.

Heavy imports are lazy so the rest of the evaluation package (dataset, report,
metrics) can be imported without the full RAG stack installed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PipelineResult:
    answer: str                                   # final user-facing answer
    raw_answer: str = ""                          # LLM output before output rail
    retrieval_context: list[str] = field(default_factory=list)   # chunks sent to LLM
    retrieved_before_rerank: int = 0
    sources: list[str] = field(default_factory=list)
    model_used: str = "none"
    retrieval_method: str = "none"
    latency_s: float = 0.0
    blocked: bool = False                         # stopped by a guardrail
    block_stage: str = ""                         # input | context
    block_reason: str = ""


async def run_pipeline(
    query: str,
    user_role: str = "user",
    use_guardrails: bool | None = None,
    guardrails=None,
) -> PipelineResult:
    """`guardrails`: optional NeMoGuardrailsManager. Evaluation jobs run in their own
    thread/event loop and pass a private instance so NeMo's async clients are never
    shared across event loops."""
    from langchain_core.output_parsers import StrOutputParser

    from app.core.config import settings
    from app.guardrails.nemo_guardrails import nemo_guardrails as _global_guardrails
    from app.rag.chain import _build_prompt
    from app.rag.llm_router import get_llm
    from app.rag.reranker.cross_encoder import reranker
    from app.rag.retriever.hybrid_retriever import HybridRetriever
    from app.rag.vectorstore import get_vectorstore

    nemo_guardrails = guardrails or _global_guardrails
    guards = settings.enable_guardrails if use_guardrails is None else use_guardrails
    t0 = time.perf_counter()
    method = "hybrid" if settings.use_hybrid_search else "semantic"

    # ── Layer 1: input rail ───────────────────────────────────────────
    if guards:
        g = await nemo_guardrails.check_input(query)
        if not g.passed:
            return PipelineResult(
                answer=g.blocked_response or g.reason,
                retrieval_method=method,
                latency_s=time.perf_counter() - t0,
                blocked=True, block_stage="input", block_reason=g.reason,
            )

    # ── Hybrid retrieval ──────────────────────────────────────────────
    retrieved = HybridRetriever(get_vectorstore()).retrieve(query, user_role)

    # ── Layer 2: context rail ─────────────────────────────────────────
    if guards:
        ctx = nemo_guardrails.check_context([d.page_content for d in retrieved])
        if not ctx.passed:
            return PipelineResult(
                answer=ctx.blocked_response,
                retrieved_before_rerank=len(retrieved),
                retrieval_method=method,
                latency_s=time.perf_counter() - t0,
                blocked=True, block_stage="context", block_reason=ctx.reason,
            )

    # ── Rerank ────────────────────────────────────────────────────────
    final_docs = (
        reranker.rerank(query, retrieved)
        if settings.use_reranker and retrieved
        else retrieved[: settings.reranker_top_n]
    )
    context_chunks = [d.page_content for d in final_docs]
    sources = list(dict.fromkeys(d.metadata.get("source", "unknown") for d in final_docs))

    # ── Generation ────────────────────────────────────────────────────
    llm = get_llm()
    raw = (_build_prompt() | llm | StrOutputParser()).invoke(
        {"context": "\n\n---\n\n".join(context_chunks), "chat_history": [], "question": query}
    )

    # ── Layer 3: output rail ──────────────────────────────────────────
    final = nemo_guardrails.check_output(raw) if guards else raw

    return PipelineResult(
        answer=final,
        raw_answer=raw,
        retrieval_context=context_chunks,
        retrieved_before_rerank=len(retrieved),
        sources=sources,
        model_used=getattr(llm, "model_name", type(llm).__name__),
        retrieval_method=method,
        latency_s=time.perf_counter() - t0,
    )
