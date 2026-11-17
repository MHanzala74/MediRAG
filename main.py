"""
RAG Medical Assistant — FastAPI entry point.
Run:  python main.py   or   uvicorn main:app --reload
"""
import time
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.config import settings
from app.utils.logger import logger
from app.api.v1 import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────
    logger.info("🚀 %s v%s starting [%s]", settings.app_name, settings.app_version, settings.app_env)
    try:
        from app.db.database import init_pool
        from app.db.models import create_tables, fail_interrupted_ingestions
        init_pool()
        create_tables()
        if (n := fail_interrupted_ingestions()):
            logger.warning("%d document(s) were still being processed at the last shutdown → marked as failed", n)
    except Exception as e:
        logger.error("DB initialization failed: %s — continuing without DB", e)

    try:
        from app.rag.vectorstore import get_vectorstore
        get_vectorstore()
        logger.info("ChromaDB initialized ✅")
    except Exception as e:
        logger.error("ChromaDB initialization failed: %s", e)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    from app.db.database import close_pool
    close_pool()
    logger.info("🛑 %s shut down", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Production-grade Medical RAG Assistant with:\n"
        "- **Multi-model RAG** (OpenAI + Groq + HuggingFace embeddings)\n"
        "- **Hybrid search** (semantic + BM25 + RRF fusion)\n"
        "- **Cross-encoder reranking**\n"
        "- **4-layer medical guardrails**\n"
        "- **JWT authentication** with role-based access control\n"
        "- **Multi-turn chat history** per session\n"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


# ── Request timing middleware ─────────────────────────────────────────
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Process-Time-Ms"] = str(ms)
    logger.debug("%s %s → %s (%sms)", request.method, request.url.path, response.status_code, ms)
    return response


# ── Global error handler ──────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Routes ────────────────────────────────────────────────────────────
app.include_router(api_router, prefix=settings.api_prefix)


# Web UI (chat, documents, evaluation dashboard) — plain static files, no build step.
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "app" / "static", html=True), name="ui")


@app.get("/", tags=["Root"], include_in_schema=False)
def root(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/ui/")          # browsers land on the UI
    return {
        "name":        settings.app_name,
        "version":     settings.app_version,
        "environment": settings.app_env,
        "ui":          "/ui/",
        "docs":        "/docs",
        "health":      f"{settings.api_prefix}/health",
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=not settings.is_production,
        log_level=settings.log_level.lower(),
    )
