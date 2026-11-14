"""
Test server for the UI end-to-end test (no DB / LLM / API keys needed).

REAL: evaluation router + background jobs + static UI.   STUBBED: auth, chat, documents, health.

    python -m uvicorn tests.ui.e2e_server:app --port 8765      # from the repo root
    cd tests/ui && npm install jsdom && node ui_e2e.js          # in a second terminal
"""
import sys, types, shutil, tempfile, asyncio, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Form, UploadFile, File, Header
from fastapi.staticfiles import StaticFiles
for m in ("auth", "documents", "chat"):
    mod = types.ModuleType(f"app.api.v1.{m}"); mod.router = APIRouter(); sys.modules[f"app.api.v1.{m}"] = mod
from app.api.v1.evaluation import router as eval_router
from app.core.security import get_current_user
from evaluation import service
from evaluation.pipeline import PipelineResult

tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir(); (tmp/"reports").mkdir()
shutil.copy(ROOT / "evaluation/data/golden_dataset.json", tmp/"data"/"golden_dataset.json")
service.DATA_DIR, service.REPORT_DIR = tmp/"data", tmp/"reports"

class M:
    def __init__(s, score, ok=True): s.score, s.success, s.reason, s.threshold = score, ok, "stub reason", 0.5
    async def a_measure(s, tc, _show_indicator=False): pass
class J:
    def get_model_name(s): return "stub-judge"
    def generate(s, p, schema=None):
        from evaluation.synthesize import _QA; return _QA(question="Q?", answer="A.")
async def pipe(q, role, g):
    await asyncio.sleep(0.15)
    if "Python" in q or "end my life" in q:
        return PipelineResult(answer="I can only help with medical questions.", blocked=True, block_stage="input", latency_s=.1)
    return PipelineResult(answer="Stub answer.", retrieval_context=["chunk one text", "chunk two text"], sources=["a.pdf"], latency_s=.4)
import evaluation.synthesize as syn
syn.load_chunks_from_chroma = lambda: [{'text': 'x'*300, 'source': 'a.pdf', 'role': 'user'}]*5
service.judge_factory = lambda: J()
service.pipeline_fn_factory = lambda: pipe
service.metrics_factory = lambda: [("Faithfulness", M(.9)), ("Bias", M(0.0)), ("MedicalCorrectness", M(.3, ok=False))]

async def who(authorization: str = Header(None)):
    tok = (authorization or "").replace("Bearer ", "")
    if tok == "admin-token": return {"username": "admin_one", "role": "admin"}
    if tok == "user-token": return {"username": "user_one", "role": "user"}
    raise HTTPException(401, "Invalid token")

app = FastAPI()
api = APIRouter(prefix="/api/v1")
api.include_router(eval_router)

@api.get("/health")
def health(): return {"status": "ok", "features": {"llm_provider": "openai", "hybrid_search": True}}
@api.post("/auth/login")
def login(username: str = Form(...), password: str = Form(...)):
    if password != "password123": raise HTTPException(401, "Invalid username or password")
    role = "admin" if username.startswith("admin") else "user"
    return {"access_token": f"{role}-token", "token_type": "bearer", "username": username, "role": role}
@api.post("/auth/signup", status_code=201)
def signup(body: dict): return {"message": "ok"}
@api.post("/chat/query")
def chat(body: dict, user=Depends(get_current_user)):
    if "python" in body["message"].lower(): raise HTTPException(422, "I can only assist with medical questions.")
    return {"answer": "Diabetes causes **thirst**.\n\n- item one\n- item two\n\n---\n⚠️ **Medical Disclaimer:** see a doctor.",
            "sources": [{"filename": "diabetes.pdf", "doc_id": "d1", "chunk_preview": "Preview text <img src=x onerror=alert(1)>"}],
            "session_id": body["session_id"], "model_used": "gpt-4o-mini", "retrieval_method": "hybrid", "guardrail_passed": True}
@api.get("/chat/history/{sid}")
def hist(sid: str, user=Depends(get_current_user)): return {"session_id": sid, "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}
@api.get("/documents/list")
def docs(user=Depends(get_current_user)):
    return {"total": 1, "documents": [{"doc_id": "d1", "filename": "diabetes.pdf", "role": "user", "chunk_count": 42, "uploaded_by": "admin_one", "created_at": "2026-11-14T10:00:00"}]}
@api.post("/documents/upload")
async def upload(file: UploadFile = File(...), role: str = Form(...), user=Depends(get_current_user)):
    return {"doc_id": "d2", "filename": file.filename, "role": role, "chunk_count": 7, "message": "ok"}
app.dependency_overrides[get_current_user] = who
app.include_router(api)
app.mount("/ui", StaticFiles(directory=ROOT / "app/static", html=True), name="ui")
