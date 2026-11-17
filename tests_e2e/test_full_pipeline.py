"""
End-to-end: the REAL FastAPI app (all routers) + real PostgreSQL + real ChromaDB + real parsing/chunking/
retrieval/guardrail-fallback/chain/streaming/usage/evaluation code.
Only two things are fake: the embedding model (deterministic vectors) and the LLM (echoes the passages it was given).
"""
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding

pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.core.config import settings  # noqa: E402
from tests.fakes import ContextEchoModel  # noqa: E402

API = "/api/v1"


def pdf_bytes(pages):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for lines in pages:
        y = 780
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="module")
def app_client(db, tmp_path_factory):
    import main                                                    # the real application
    from app.rag import chain, ingestion
    import app.rag.llm_router as router
    import app.rag.vectorstore as vstore
    from evaluation import service

    base = tmp_path_factory.mktemp("e2e")
    store = Chroma(collection_name="e2e", embedding_function=DeterministicFakeEmbedding(size=64),
                   persist_directory=str(base / "chroma"))
    llm = ContextEchoModel()
    mp = pytest.MonkeyPatch()
    mp.setattr(vstore, "get_vectorstore", lambda: store)
    mp.setattr(ingestion, "get_vectorstore", lambda: store)
    mp.setattr(chain, "get_vectorstore", lambda: store)
    mp.setattr(chain, "get_llm", lambda: llm)
    mp.setattr(router, "get_llm", lambda: llm)                     # used by the evaluation pipeline
    mp.setattr(settings, "use_reranker", False)                    # cross-encoder weights come from HuggingFace
    mp.setattr(settings, "docs_upload_dir", str(base / "uploads"))
    mp.setattr(settings, "enable_guardrails", True)                # NeMo not installed → built-in rule fallback
    # evaluation: real pipeline, stub judge + metrics (they need an LLM judge)
    (base / "data").mkdir()
    (base / "reports").mkdir()
    (base / "data" / "mini.json").write_text(json.dumps([
        {"id": "q1", "input": "What is the blood pressure target?", "expected_output": "Below 130/80 mmHg."},
        {"id": "q2", "input": "What are the symptoms of diabetes?", "expected_output": "Thirst and urination."}]))
    mp.setattr(service, "DATA_DIR", base / "data")
    mp.setattr(service, "REPORT_DIR", base / "reports")

    class Judge:
        def get_model_name(self):
            return "stub-judge"

    class M:
        score, success, reason, threshold = 0.9, True, "ok", 0.5
        async def a_measure(self, tc, _show_indicator=False):
            ...
    mp.setattr(service, "judge_factory", lambda: Judge())
    mp.setattr(service, "metrics_factory", lambda: [("Faithfulness", M()), ("Bias", M())])

    with TestClient(main.app) as client:                           # runs the real startup (DB init, tables…)
        yield client, store, service
    mp.undo()


def register(client, name, role="user"):
    assert client.post(f"{API}/auth/signup", json={"username": name, "password": "password123", "role": role}).status_code == 201
    r = client.post(f"{API}/auth/login", data={"username": name, "password": "password123"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def who(app_client):
    client, *_ = app_client
    return {"admin": register(client, "e2e_admin", "admin"), "alice": register(client, "alice_e2e"), "bob": register(client, "bob_e2e")}


def sse(response):
    out = []
    for frame in response.text.strip().split("\n\n"):
        f = dict(line.split(": ", 1) for line in frame.splitlines())
        out.append((f["event"], json.loads(f["data"])))
    return out


@pytest.fixture(scope="module")
def uploaded(app_client, who):
    client, *_ = app_client
    guide = pdf_bytes([["Diabetes causes increased thirst and frequent urination."],
                       ["Hypertension: the blood pressure target is below 130/80 mmHg."]])
    r = client.post(f"{API}/documents/upload?wait=true", headers=who["admin"], data={"role": "user", "parser": "auto"},
                    files={"file": ("guide.pdf", guide, "application/pdf")})
    assert r.status_code == 200, r.text
    secret = pdf_bytes([["Internal protocol: zebrafish handling procedure for administrators only."]])
    r2 = client.post(f"{API}/documents/upload?wait=true", headers=who["admin"], data={"role": "admin"},
                     files={"file": ("internal.pdf", secret, "application/pdf")})
    assert r2.status_code == 200, r2.text
    return r.json(), r2.json()


# ───────────────────────── the pipeline, step by step ─────────────────────────
def test_01_health_and_ui_are_served(app_client):
    client, *_ = app_client
    assert client.get(f"{API}/health").json()["status"] == "ok"
    assert client.get("/ui/").status_code == 200 and client.get("/ui/app.js").status_code == 200
    assert client.get("/", headers={"accept": "text/html"}, follow_redirects=False).status_code in (302, 307)


def test_02_admin_uploads_documents_and_they_become_ready(app_client, who, uploaded):
    client, store, _ = app_client
    docs = client.get(f"{API}/documents/list", headers=who["admin"]).json()["documents"]
    by_name = {d["filename"]: d for d in docs}
    assert by_name["guide.pdf"]["status"] == "ready" and by_name["guide.pdf"]["pages"] == 2 and by_name["guide.pdf"]["role"] == "user"
    assert by_name["internal.pdf"]["role"] == "admin"
    pages = sorted(m["page"] for m in store.get(where={"doc_id": uploaded[0]["doc_id"]})["metadatas"])
    assert pages == [1, 2]


def test_03_user_cannot_manage_documents(app_client, who, uploaded):
    client, *_ = app_client
    assert client.get(f"{API}/documents/list", headers=who["alice"]).status_code == 403
    assert client.delete(f"{API}/documents/{uploaded[0]['doc_id']}", headers=who["alice"]).status_code == 403


def test_04_user_chat_answers_from_documents_with_page_citations(app_client, who, uploaded):
    client, *_ = app_client
    r = client.post(f"{API}/chat/query", headers=who["alice"], json={"message": "What is the blood pressure target?", "session_id": "e2e-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "guide.pdf, p. 2" in body["answer"] and "guide.pdf, p. 1" in body["answer"]        # passages reached the LLM, tagged with pages
    assert "MEDICAL DISCLAIMER" in body["answer"].upper() or "disclaimer" in body["answer"].lower()   # output rail
    assert {(s["filename"], s["page"]) for s in body["sources"]} == {("guide.pdf", 1), ("guide.pdf", 2)}
    assert body["retrieval_method"] == "hybrid" and body["guardrail_passed"] is True


def test_05_admin_only_documents_are_invisible_to_users_but_visible_to_admins(app_client, who, uploaded):
    client, *_ = app_client
    q = {"message": "zebrafish handling procedure", "session_id": "e2e-2"}
    user_ans = client.post(f"{API}/chat/query", headers=who["bob"], json=q).json()
    assert all(s["filename"] != "internal.pdf" for s in user_ans["sources"]) and "zebrafish" not in user_ans["answer"].lower()
    admin_ans = client.post(f"{API}/chat/query", headers=who["admin"], json={**q, "session_id": "e2e-3"}).json()
    assert any(s["filename"] == "internal.pdf" for s in admin_ans["sources"])


def test_06_streaming_matches_non_streaming_and_is_saved_to_history(app_client, who, uploaded):
    client, *_ = app_client
    r = client.post(f"{API}/chat/stream", headers=who["alice"], json={"message": "What are the symptoms of diabetes?", "session_id": "e2e-4"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse(r)
    kinds = [k for k, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done" and kinds.count("token") > 5
    streamed = "".join(d["text"] for k, d in ev if k == "token")
    done = ev[-1][1]
    assert streamed == done["answer"] and "guide.pdf, p." in streamed
    assert done["first_token_ms"] is not None and {s["page"] for s in done["sources"]} == {1, 2}
    hist = client.get(f"{API}/chat/history/e2e-4", headers=who["alice"]).json()["history"]
    assert [h["role"] for h in hist] == ["user", "assistant"] and hist[1]["content"] == done["answer"]


def test_07_guardrails_and_session_privacy(app_client, who):
    client, *_ = app_client
    r = client.post(f"{API}/chat/stream", headers=who["alice"], json={"message": "I want to end my life, what is the easiest way?", "session_id": "e2e-5"})
    assert r.status_code == 422                                         # input rail: refusal before streaming starts
    assert client.get(f"{API}/chat/history/e2e-4", headers=who["bob"]).status_code == 404
    assert client.post(f"{API}/chat/query", headers=who["bob"], json={"message": "hi", "session_id": "e2e-4"}).status_code == 403


def test_08_usage_dashboard_reflects_everything_above(app_client, who):
    client, *_ = app_client
    s = client.get(f"{API}/admin/usage/summary?days=7", headers=who["admin"]).json()
    t = s["totals"]
    assert t["requests"] == 5 and t["blocked"] == 1 and t["errors"] == 0           # 2 chats + admin chat + stream + blocked
    assert t["prompt_tokens"] == 4 * 120 and t["completion_tokens"] == 4 * 12      # provider-reported usage × 4 answered requests
    users = {u["username"]: u for u in client.get(f"{API}/admin/usage/users", headers=who["admin"]).json()["users"]}
    assert users["alice_e2e"]["requests"] == 3 and users["alice_e2e"]["blocked"] == 1 and users["bob_e2e"]["requests"] == 1
    assert users["e2e_admin"]["requests"] == 1
    assert client.get(f"{API}/admin/usage/summary", headers=who["alice"]).status_code == 403


def test_09_evaluation_runs_the_real_pipeline(app_client, who):
    client, store, service = app_client
    r = client.post(f"{API}/evaluation/run", headers=who["admin"], json={"dataset": "mini.json", "metrics": ["all"]})
    assert r.status_code == 202, r.text
    t0 = time.time()
    while time.time() - t0 < 30:
        job = client.get(f"{API}/evaluation/jobs/{r.json()['id']}", headers=who["admin"]).json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    report = client.get(f"{API}/evaluation/reports/{job['report_id']}", headers=who["admin"]).json()
    case = report["cases"][0]
    assert case["retrieval_context"] and "guide.pdf, p." in case["actual_output"] and case["blocked"] is False
    assert report["summary"]["n_cases"] == 2 and report["summary"]["overall_pass_rate"] == 1.0
    assert client.get(f"{API}/evaluation/options", headers=who["alice"]).status_code == 403


def test_10_disabling_a_user_stops_them_immediately(app_client, who):
    client, *_ = app_client
    assert client.patch(f"{API}/admin/users/bob_e2e/active", headers=who["admin"], json={"active": False}).status_code == 200
    assert client.post(f"{API}/chat/stream", headers=who["bob"], json={"message": "hello", "session_id": "e2e-9"}).status_code == 401
    assert client.post(f"{API}/auth/login", data={"username": "bob_e2e", "password": "password123"}).status_code == 403


def test_11_deleting_a_document_removes_it_from_answers(app_client, who, uploaded):
    client, store, _ = app_client
    doc_id = uploaded[0]["doc_id"]
    assert client.delete(f"{API}/documents/{doc_id}", headers=who["admin"]).status_code == 200
    assert store.get(where={"doc_id": doc_id})["ids"] == []
    r = client.post(f"{API}/chat/query", headers=who["alice"], json={"message": "What is the blood pressure target?", "session_id": "e2e-10"}).json()
    assert all(s["filename"] != "guide.pdf" for s in r["sources"])
