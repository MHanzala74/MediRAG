"""Chat endpoints — query RAG pipeline and get history."""
from fastapi import APIRouter, Depends, HTTPException
from app.core.security import get_current_user
from app.rag.chain import run_rag_chain
from app.db.models import get_history
from app.schemas.chat import ChatRequest, ChatResponse, ChatHistoryResponse, Source, HistoryItem
from app.utils.exceptions import GuardrailException, RAGException
from app.utils.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/query", response_model=ChatResponse, summary="Ask a medical question")
async def chat_query(req: ChatRequest, user: dict = Depends(get_current_user)):
    logger.info(
        "Chat query | user=%s | role=%s | session=%s | query=%.60s...",
        user["username"], user["role"], req.session_id, req.message
    )
    try:
        result = await run_rag_chain(
            query=req.message,
            user_role=user["role"],
            session_id=req.session_id,
        )
    except GuardrailException as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RAGException as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("Unexpected error in chat: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

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
    history = get_history(session_id, limit=50)
    return ChatHistoryResponse(
        session_id=session_id,
        history=[HistoryItem(**h) for h in history],
    )
