"""Health check endpoint."""
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter(tags=["Health"])

@router.get("/health", summary="System health check")
def health():
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.app_env,
        "features": {
            "hybrid_search": settings.use_hybrid_search,
            "reranker":      settings.use_reranker,
            "guardrails":    settings.enable_guardrails,
            "llm_provider":  settings.primary_llm,
            "embeddings":    settings.embedding_provider,
        }
    }
