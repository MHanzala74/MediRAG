"""Chat endpoints — query RAG pipeline and get history."""
import asyncio
import json
import time
from contextlib import aclosing

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.core.security import get_current_user
from app.rag.chain import run_rag_chain, prepare_rag, stream_rag, llm_model_name
from app.rag.llm_router import provider_name
from app.db.models import get_history, claim_session, session_owner
from app.db.usage import record_usage
from app.schemas.chat import ChatRequest, ChatResponse, ChatHistoryResponse, Source, HistoryItem
from app.utils.exceptions import GuardrailException, RAGException
from app.utils.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/query", response_model=ChatResponse, summary="Ask a medical question")
async def chat_query(req: ChatRequest, user: dict = Depends(get_current_user)):
    logger.info(
        "Chat query | user=%s | role=%s | session=%s | chars=%d",
        user["username"], user["role"], req.session_id, len(req.message)
    )
    # A chat session belongs to the user who started it.
    if not claim_session(req.session_id, user["username"], user["role"]):
        raise HTTPException(status_code=403, detail="This chat session belongs to another user")

    t0 = time.perf_counter()

    def log_usage(status: str, **extra):
        record_usage(
            username=user["username"], role=user["role"], session_id=req.session_id, status=status,
            latency_ms=int((time.perf_counter() - t0) * 1000), query_chars=len(req.message), **extra,
        )

    try:
        result = await run_rag_chain(
            query=req.message,
            user_role=user["role"],
            session_id=req.session_id,
        )
    except GuardrailException as e:
        log_usage("blocked")
        raise HTTPException(status_code=422, detail=str(e))
    except RAGException as e:
        log_usage("error", provider=provider_name())
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        log_usage("error", provider=provider_name())
        logger.error("Unexpected error in chat: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    usage = result.get("usage", {})
    log_usage(
        "ok" if result["guardrail_passed"] else "blocked",
        provider=result.get("provider"),
        model=None if result["model_used"] == "none" else result["model_used"],
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        tokens_estimated=usage.get("tokens_estimated", False),
        retrieval_method=result["retrieval_method"],
        sources_count=len(result["sources"]),
        answer_chars=len(result["answer"]),
    )

    return ChatResponse(
        answer=result["answer"],
        sources=[Source(**s) for s in result["sources"]],
        session_id=req.session_id,
        model_used=result["model_used"],
        retrieval_method=result["retrieval_method"],
        guardrail_passed=result["guardrail_passed"],
    )


def _log_usage(user: dict, req: ChatRequest, t0: float, status: str, **extra):
    record_usage(
        username=user["username"], role=user["role"], session_id=req.session_id, status=status,
        latency_ms=int((time.perf_counter() - t0) * 1000), query_chars=len(req.message), **extra,
    )


def _sse(event: str, data: dict) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_events(req: ChatRequest, user: dict, prepared, t0: float):
    """
    Event stream for /chat/stream:

        meta     {session_id, sources, retrieval_method, model_used}
        token    {text}              repeated; incremental answer text
        replace  {text}              (rare) the output guardrail rewrote the answer: show this instead
        done     {answer, sources, model_used, retrieval_method, guardrail_passed, first_token_ms, total_ms}
        error    {detail}            generation failed after the stream started
    """
    blocked = prepared.blocked_result
    yield _sse("meta", {
        "session_id": req.session_id, "sources": prepared.sources, "retrieval_method": prepared.method,
        "model_used": "none" if blocked else llm_model_name(),
    })

    if blocked:                                          # context rail: refusal text, no LLM call
        yield _sse("token", {"text": blocked["answer"]})
        _log_usage(user, req, t0, "blocked", retrieval_method=prepared.method, answer_chars=len(blocked["answer"]))
        yield _sse("done", {"answer": blocked["answer"], "sources": [], "model_used": "none",
                            "retrieval_method": prepared.method, "guardrail_passed": False,
                            "first_token_ms": None, "total_ms": int((time.perf_counter() - t0) * 1000)})
        return

    streamed_chars, first_token_ms, logged = 0, None, False
    try:
        async with aclosing(stream_rag(req.message, req.session_id, prepared)) as events:
            async for ev in events:
                if ev["type"] == "token":
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - t0) * 1000)
                    streamed_chars += len(ev["text"])
                    yield _sse("token", {"text": ev["text"]})
                elif ev["type"] == "replace":
                    yield _sse("replace", {"text": ev["text"]})
                elif ev["type"] == "done":
                    result, usage = ev["result"], ev["result"]["usage"]
                    _log_usage(
                        user, req, t0, "ok", provider=result["provider"], model=result["model_used"],
                        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
                        tokens_estimated=usage["tokens_estimated"], retrieval_method=result["retrieval_method"],
                        sources_count=len(result["sources"]), answer_chars=len(result["answer"]),
                    )
                    logged = True
                    total_ms = int((time.perf_counter() - t0) * 1000)
                    logger.info("Stream complete | user=%s | first_token_ms=%s | total_ms=%d", user["username"], first_token_ms, total_ms)
                    yield _sse("done", {
                        "answer": result["answer"], "sources": result["sources"], "model_used": result["model_used"],
                        "retrieval_method": result["retrieval_method"], "guardrail_passed": True,
                        "first_token_ms": first_token_ms, "total_ms": total_ms,
                    })
    except (asyncio.CancelledError, GeneratorExit):
        # Client pressed Stop / disconnected: the LLM call is cancelled. Nothing is saved to chat
        # history, but the tokens already generated are logged (estimated, ~4 chars per token).
        if not logged:
            _log_usage(
                user, req, t0, "cancelled", provider=provider_name(), model=llm_model_name(),
                prompt_tokens=(prepared.prompt_chars + 3) // 4, completion_tokens=(streamed_chars + 3) // 4,
                tokens_estimated=True, retrieval_method=prepared.method, sources_count=len(prepared.sources),
                answer_chars=streamed_chars,
            )
            logger.info("Stream cancelled by client | user=%s | chars=%d", user["username"], streamed_chars)
        raise
    except Exception as e:
        _log_usage(user, req, t0, "error", provider=provider_name())
        logger.error("Error while streaming: %s", e, exc_info=True)
        detail = str(e) if isinstance(e, RAGException) else "Internal server error"
        yield _sse("error", {"detail": detail})


@router.post("/stream", summary="Ask a medical question — streamed answer (Server-Sent Events)")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    """
    Same pipeline as `/chat/query`, but the answer is delivered token by token as `text/event-stream`.
    Authentication, session-ownership and guardrail failures are still plain HTTP errors (401/403/422),
    returned *before* any streaming starts.
    """
    logger.info("Chat stream | user=%s | role=%s | session=%s | chars=%d",
                user["username"], user["role"], req.session_id, len(req.message))
    if not claim_session(req.session_id, user["username"], user["role"]):
        raise HTTPException(status_code=403, detail="This chat session belongs to another user")

    t0 = time.perf_counter()
    try:
        prepared = await prepare_rag(req.message, user["role"], req.session_id)
    except GuardrailException as e:
        _log_usage(user, req, t0, "blocked")
        raise HTTPException(status_code=422, detail=str(e))
    except RAGException as e:
        _log_usage(user, req, t0, "error", provider=provider_name())
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        _log_usage(user, req, t0, "error", provider=provider_name())
        logger.error("Unexpected error preparing stream: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    return StreamingResponse(
        _sse_events(req, user, prepared, t0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},   # no proxy buffering
    )


@router.get("/history/{session_id}", response_model=ChatHistoryResponse, summary="Get chat history")
def chat_history(session_id: str, user: dict = Depends(get_current_user)):
    # Only the owner may read a session (404, not 403, so session ids are not probeable).
    if session_owner(session_id) != user["username"]:
        raise HTTPException(status_code=404, detail="Session not found")
    history = get_history(session_id, limit=50)
    return ChatHistoryResponse(
        session_id=session_id,
        history=[HistoryItem(**h) for h in history],
    )
