"""
UI ↔ the REAL application: `main.app` with every router, real PostgreSQL, real ChromaDB, real parsing /
chunking / retrieval / guardrail-fallback / chain / streaming / usage logging.
Only the LLM (echoes the passages it receives) and the embedding model are fake.

    POSTGRES_PASSWORD=... python -m uvicorn tests.ui.real_backend_server:app --port 8766
    cd tests/ui && node ui_real_e2e.js        # uploads /tmp/medirag_ui_sample.pdf through the UI
"""
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg2
from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.core.config import settings

# fresh database + folders for this run
admin = psycopg2.connect(host=settings.postgres_host, port=settings.postgres_port, user=settings.postgres_user,
                         password=settings.postgres_password, dbname="postgres")
admin.autocommit = True
admin.cursor().execute('DROP DATABASE IF EXISTS "medirag_ui_e2e" WITH (FORCE)')
admin.cursor().execute('CREATE DATABASE "medirag_ui_e2e"')
tmp = Path(tempfile.mkdtemp())
settings.postgres_db = "medirag_ui_e2e"
settings.admin_signup_code = ""                 # development mode: admin signup allowed without a code
settings.use_reranker = False                   # cross-encoder weights are downloaded from HuggingFace
settings.docs_upload_dir = str(tmp / "uploads")

# a small two-page PDF for the browser test to upload
buf = io.BytesIO()
c = canvas.Canvas(buf, pagesize=A4)
for line in ("Diabetes causes increased thirst and frequent urination.", "Hypertension: the blood pressure target is below 130/80 mmHg."):
    c.drawString(72, 780, line)
    c.showPage()
c.save()
Path("/tmp/medirag_ui_sample.pdf").write_bytes(buf.getvalue())

import app.rag.chain as chain
import app.rag.ingestion as ingestion
import app.rag.llm_router as router
import app.rag.vectorstore as vstore
from tests.fakes import ContextEchoModel

store = Chroma(collection_name="ui_e2e", embedding_function=DeterministicFakeEmbedding(size=64), persist_directory=str(tmp / "chroma"))
llm = ContextEchoModel()
vstore.get_vectorstore = lambda: store
ingestion.get_vectorstore = lambda: store
chain.get_vectorstore = lambda: store
chain.get_llm = lambda: llm
router.get_llm = lambda: llm

from main import app  # noqa: E402  (the real application, UI included)
