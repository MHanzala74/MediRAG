"""Chat endpoints — query RAG pipeline and get history."""
import time
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.rag.chain import run_rag_chain
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
