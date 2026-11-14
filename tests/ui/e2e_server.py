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

USERS = {  # in-memory accounts for the stub
    "admin_one": {"role": "admin", "is_active": True}, "user_one": {"role": "user", "is_active": True},
    "alice": {"role": "user", "is_active": True}, "bob": {"role": "user", "is_active": True}, "carol": {"role": "user", "is_active": True},
}
TOKENS = {"admin-token": "admin_one", "user-token": "user_one"}

async def who(authorization: str = Header(None)):
    name = TOKENS.get((authorization or "").replace("Bearer ", ""))
    if not name: raise HTTPException(401, "Invalid token")
    if not USERS[name]["is_active"]: raise HTTPException(401, "This account is disabled or no longer exists.")
    return {"username": name, "role": USERS[name]["role"]}

async def admin_only(user=Depends(who)):
    if user["role"] != "admin": raise HTTPException(403, "Admin access required")
    return user

app = FastAPI()
api = APIRouter(prefix="/api/v1")
api.include_router(eval_router)

@api.get("/health")
def health(): return {"status": "ok", "features": {"llm_provider": "openai", "hybrid_search": True}}
@api.post("/auth/login")
def login(username: str = Form(...), password: str = Form(...)):
    if password != "password123": raise HTTPException(401, "Invalid username or password")
    role = "admin" if username.startswith("admin") else "user"
    if username in USERS and not USERS[username]["is_active"]:
        raise HTTPException(403, "This account has been disabled. Contact an administrator.")
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
def docs(user=Depends(admin_only)):
    return {"total": 1, "documents": [{"doc_id": "d1", "filename": "diabetes.pdf", "role": "user", "chunk_count": 42, "uploaded_by": "admin_one", "created_at": "2026-11-14T10:00:00"}]}
@api.post("/documents/upload")
async def upload(file: UploadFile = File(...), role: str = Form(...), user=Depends(admin_only)):
    return {"doc_id": "d2", "filename": file.filename, "role": role, "chunk_count": 7, "message": "ok"}

def _u(name, role, req, blocked, err, pt, ct, cost, lat, active=True):
    return {"username": name, "role": role, "is_active": active, "created_at": "2026-11-01T10:00:00+00:00", "last_login": "2026-11-14T09:00:00+00:00" if req else None,
            "requests": req, "blocked": blocked, "errors": err, "prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct,
            "cost_usd": cost, "avg_latency_ms": lat, "last_request": "2026-11-14T09:30:00+00:00" if req else None}
USAGE = {"alice": (12, 1, 0, 24000, 4800, 0.0065, 2100), "bob": (3, 0, 1, 6000, 900, 0.0014, 1800), "carol": (0, 0, 0, 0, 0, 0, 0), "admin_one": (2, 0, 0, 3000, 500, 0.0007, 1500)}

@api.get("/admin/usage/summary")
def usage_summary(days: int = 7, admin=Depends(admin_only)):
    return {"days": days, "totals": {"requests": 17, "active_users": 3, "prompt_tokens": 33000, "completion_tokens": 6200, "total_tokens": 39200,
            "cost_usd": 0.0086, "avg_latency_ms": 1950, "blocked": 1, "errors": 1, "unpriced_requests": 0},
            "users": {"regular": 4, "admins": 1, "disabled": sum(1 for u in USERS.values() if not u["is_active"]), "total": 5},
            "daily": [{"date": f"2026-11-{8 + i:02d}", "requests": r, "tokens": r * 2300, "users": 1 + (r > 0)} for i, r in enumerate([0, 2, 5, 0, 3, 4, 3])],
            "by_model": [{"provider": "openai", "model": "gpt-4o-mini", "requests": 15, "prompt_tokens": 33000, "completion_tokens": 6200, "total_tokens": 39200, "cost_usd": 0.0086},
                         {"provider": "-", "model": "-", "requests": 2, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0}]}

@api.get("/admin/usage/users")
def usage_users(days: int = 7, admin=Depends(admin_only)):
    rows = [_u(n, USERS[n]["role"], *USAGE.get(n, (0,) * 7), active=USERS[n]["is_active"]) for n in ("alice", "bob", "admin_one", "carol", "user_one") if n in USERS]
    return {"days": days, "users": sorted(rows, key=lambda r: -r["requests"])}

@api.get("/admin/usage/recent")
def usage_recent(limit: int = 50, username: str | None = None, admin=Depends(admin_only)):
    names = [username] if username else ["alice", "bob"]
    return {"requests": [{"created_at": "2026-11-14T09:30:00+00:00", "username": n, "status": "ok", "model": "gpt-4o-mini", "total_tokens": 2300,
                          "latency_ms": 2100, "cost_usd": 0.0005, "retrieval_method": "hybrid", "sources_count": 2} for n in names]}

@api.patch("/admin/users/{username}/active")
def set_active(username: str, body: dict, admin=Depends(admin_only)):
    if username == admin["username"]: raise HTTPException(400, "You cannot change your own account status")
    if username not in USERS: raise HTTPException(404, "User not found")
    USERS[username]["is_active"] = bool(body["active"])
    return {"username": username, "is_active": USERS[username]["is_active"]}

app.dependency_overrides[get_current_user] = who
app.include_router(api)
app.mount("/ui", StaticFiles(directory=ROOT / "app/static", html=True), name="ui")
