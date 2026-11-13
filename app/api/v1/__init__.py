"""API v1 router aggregator."""
from fastapi import APIRouter
from app.api.v1.auth      import router as auth_router
from app.api.v1.documents import router as docs_router
from app.api.v1.chat      import router as chat_router
from app.api.v1.health    import router as health_router
from app.api.v1.evaluation import router as evaluation_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(docs_router)
api_router.include_router(chat_router)
api_router.include_router(evaluation_router)
