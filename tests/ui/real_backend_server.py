"""
UI ↔ REAL backend: real auth, JWT, admin/usage API, usage logging and PostgreSQL.
Only the RAG chain (needs an LLM) and document ingestion are stubbed.

    POSTGRES_PASSWORD=... python -m uvicorn tests.ui.real_backend_server:app --port 8766
    cd tests/ui && node ui_real_e2e.js
"""
import asyncio, sys, types
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg2
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.utils.exceptions import GuardrailException

# fresh database for this run
admin = psycopg2.connect(host=settings.postgres_host, port=settings.postgres_port, user=settings.postgres_user,
                         password=settings.postgres_password, dbname="postgres")
admin.autocommit = True
admin.cursor().execute('DROP DATABASE IF EXISTS "medirag_ui_e2e" WITH (FORCE)')
admin.cursor().execute('CREATE DATABASE "medirag_ui_e2e"')
settings.postgres_db = "medirag_ui_e2e"
settings.admin_signup_code = ""        # development mode: admin signup allowed without a code

async def fake_chain(query, user_role, session_id):
    if "python" in query.lower():
        raise GuardrailException("I can only assist with medical questions.")
    return {"answer": "Stub answer.", "sources": [{"filename": "a.pdf", "doc_id": "d", "chunk_preview": "p"}],
            "model_used": "gpt-4o-mini-2024-07-18", "retrieval_method": "hybrid", "guardrail_passed": True,
            "provider": "openai", "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "tokens_estimated": False}}

class Prepared:
    def __init__(self):
        self.inputs, self.method, self.n_docs, self.prompt_chars, self.blocked_result = {}, "hybrid", 1, 400, None
        self.sources = [{"filename": "a.pdf", "doc_id": "d", "chunk_preview": "p"}]

async def fake_prepare(query, user_role, session_id):
    if "python" in query.lower():
        raise GuardrailException("I can only assist with medical questions.")
    return Prepared()

async def fake_stream(query, session_id, prepared):
    for w in ["Stub ", "answer", "."]:
        await asyncio.sleep(0.05)
        yield {"type": "token", "text": w}
    yield {"type": "done", "result": {"answer": "Stub answer.", "sources": prepared.sources, "model_used": "gpt-4o-mini-2024-07-18",
           "retrieval_method": "hybrid", "guardrail_passed": True, "provider": "openai",
           "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "tokens_estimated": False}}}

chain = types.ModuleType("app.rag.chain")
chain.run_rag_chain, chain.prepare_rag, chain.stream_rag = fake_chain, fake_prepare, fake_stream
chain.llm_model_name = lambda: "gpt-4o-mini-2024-07-18"
sys.modules["app.rag.chain"] = chain
docs = types.ModuleType("app.api.v1.documents"); docs.router = APIRouter(); sys.modules["app.api.v1.documents"] = docs

from app.api.v1 import api_router
from app.db.database import init_pool
from app.db.models import create_tables

init_pool(); create_tables()
app = FastAPI()
app.include_router(api_router, prefix="/api/v1")
app.mount("/ui", StaticFiles(directory=ROOT / "app/static", html=True), name="ui")
