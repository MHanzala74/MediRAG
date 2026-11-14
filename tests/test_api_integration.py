"""
HTTP-level integration test: REAL auth, REAL JWT/security, REAL admin router, REAL PostgreSQL.
Only the RAG chain and document ingestion are stubbed (they need an LLM and embedding models).
"""
import sys
import types

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.utils.exceptions import GuardrailException, RAGException

# ── stub the heavy modules BEFORE the routers import them ──────────────
CHAIN_BEHAVIOUR = {"mode": "ok"}


async def fake_chain(query, user_role, session_id):
    mode = CHAIN_BEHAVIOUR["mode"]
    if mode == "blocked":
        raise GuardrailException("I can only assist with medical questions.")
    if mode == "error":
        raise RAGException(RuntimeError("llm down"))
    return {"answer": "Stub answer", "sources": [{"filename": "a.pdf", "doc_id": "d", "chunk_preview": "p"}],
            "model_used": "gpt-4o-mini-2024-07-18", "retrieval_method": "hybrid", "guardrail_passed": True,
            "provider": "openai", "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "tokens_estimated": False}}


chain_mod = types.ModuleType("app.rag.chain")
chain_mod.run_rag_chain = fake_chain
sys.modules["app.rag.chain"] = chain_mod
docs_mod = types.ModuleType("app.api.v1.documents")
docs_mod.router = APIRouter()
sys.modules["app.api.v1.documents"] = docs_mod

from app.api.v1 import api_router  # noqa: E402
from app.db import models  # noqa: E402


@pytest.fixture(scope="module")
def client(db):
    models.create_tables()
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


API = "/api/v1"


def signup_login(c, name, role="user", code=None):
    body = {"username": name, "password": "password123", "role": role}
    if code:
        body["admin_code"] = code
    assert c.post(f"{API}/auth/signup", json=body).status_code == 201
    r = c.post(f"{API}/auth/login", data={"username": name, "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="module")
def tokens(client):
    return {"admin": signup_login(client, "root_admin", "admin"),
            "alice": signup_login(client, "alice_user"),
            "bob": signup_login(client, "bob_user")}


def ask(c, who, session, msg="what is diabetes?"):
    return c.post(f"{API}/chat/query", json={"message": msg, "session_id": session}, headers=who)


def test_chat_requires_authentication(client):
    assert client.post(f"{API}/chat/query", json={"message": "hi", "session_id": "s"}).status_code == 401
    assert client.get(f"{API}/admin/usage/summary").status_code == 401
    bad = {"Authorization": "Bearer not.a.jwt"}
    assert client.post(f"{API}/chat/query", json={"message": "hi", "session_id": "s"}, headers=bad).status_code == 401


def test_wrong_password_rejected(client, tokens):
    r = client.post(f"{API}/auth/login", data={"username": "alice_user", "password": "wrong-password"})
    assert r.status_code == 401


def test_chat_records_usage(client, tokens):
    CHAIN_BEHAVIOUR["mode"] = "ok"
    assert ask(client, tokens["alice"], "sess-alice").status_code == 200
    assert ask(client, tokens["alice"], "sess-alice").status_code == 200
    CHAIN_BEHAVIOUR["mode"] = "blocked"
    r = ask(client, tokens["alice"], "sess-alice", "write python")
    assert r.status_code == 422 and "medical" in r.json()["detail"]
    CHAIN_BEHAVIOUR["mode"] = "error"
    assert ask(client, tokens["bob"], "sess-bob").status_code == 500
    CHAIN_BEHAVIOUR["mode"] = "ok"
    assert ask(client, tokens["bob"], "sess-bob").status_code == 200


def test_admin_dashboard_numbers(client, tokens):
    s = client.get(f"{API}/admin/usage/summary?days=7", headers=tokens["admin"]).json()
    t = s["totals"]
    assert t["requests"] == 5 and t["active_users"] == 2
    assert (t["prompt_tokens"], t["completion_tokens"], t["total_tokens"]) == (3000, 600, 3600)
    assert t["blocked"] == 1 and t["errors"] == 1 and t["cost_usd"] > 0
    assert s["users"]["regular"] == 2 and s["users"]["admins"] == 1
    assert s["by_model"][0]["model"] == "gpt-4o-mini-2024-07-18"

    users = {u["username"]: u for u in client.get(f"{API}/admin/usage/users", headers=tokens["admin"]).json()["users"]}
    assert users["alice_user"]["requests"] == 3 and users["alice_user"]["blocked"] == 1
    assert users["alice_user"]["total_tokens"] == 2400 and users["alice_user"]["last_login"] is not None
    assert users["bob_user"]["requests"] == 2 and users["bob_user"]["errors"] == 1
    assert users["root_admin"]["requests"] == 0

    recent = client.get(f"{API}/admin/usage/recent?limit=10&username=ALICE_user", headers=tokens["admin"]).json()["requests"]
    assert len(recent) == 3 and all(r["username"] == "alice_user" for r in recent)
    assert all("message" not in r and "answer" not in r for r in recent)       # no chat content in analytics


def test_users_cannot_see_admin_endpoints(client, tokens):
    for path in ("/admin/usage/summary", "/admin/usage/users", "/admin/usage/recent", "/evaluation/options"):
        assert client.get(API + path, headers=tokens["alice"]).status_code == 403, path
    r = client.patch(f"{API}/admin/users/bob_user/active", json={"active": False}, headers=tokens["alice"])
    assert r.status_code == 403


def test_sessions_are_private(client, tokens):
    assert ask(client, tokens["bob"], "sess-alice").status_code == 403           # cannot hijack Alice's session
    assert client.get(f"{API}/chat/history/sess-alice", headers=tokens["bob"]).status_code == 404
    assert client.get(f"{API}/chat/history/sess-alice", headers=tokens["alice"]).status_code == 200
    assert client.get(f"{API}/chat/history/sess-alice", headers=tokens["admin"]).status_code == 404   # even admins


def test_session_id_is_bounded(client, tokens):
    assert ask(client, tokens["alice"], "x" * 101).status_code == 422


def test_disabling_a_user_takes_effect_immediately(client, tokens):
    assert client.patch(f"{API}/admin/users/bob_user/active", json={"active": False}, headers=tokens["admin"]).status_code == 200
    # bob's still-valid JWT stops working at once …
    assert ask(client, tokens["bob"], "sess-bob").status_code == 401
    # … and he cannot log in again
    r = client.post(f"{API}/auth/login", data={"username": "bob_user", "password": "password123"})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]
    users = {u["username"]: u for u in client.get(f"{API}/admin/usage/users", headers=tokens["admin"]).json()["users"]}
    assert users["bob_user"]["is_active"] is False
    # re-enable
    assert client.patch(f"{API}/admin/users/bob_user/active", json={"active": True}, headers=tokens["admin"]).status_code == 200
    assert ask(client, tokens["bob"], "sess-bob").status_code == 200


def test_admin_cannot_disable_self_or_unknown_user(client, tokens):
    assert client.patch(f"{API}/admin/users/root_admin/active", json={"active": False}, headers=tokens["admin"]).status_code == 400
    assert client.patch(f"{API}/admin/users/ghost_user/active", json={"active": False}, headers=tokens["admin"]).status_code == 404


def test_role_change_applies_immediately(client, tokens, db):
    extra = signup_login(client, "temp_admin", "admin")
    assert client.get(f"{API}/admin/usage/summary", headers=extra).status_code == 200
    conn = models.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET role='user' WHERE username='temp_admin'")
        conn.commit()
    finally:
        models.release_connection(conn)
    assert client.get(f"{API}/admin/usage/summary", headers=extra).status_code == 403    # token still says admin; DB wins


def test_days_validation(client, tokens):
    assert client.get(f"{API}/admin/usage/summary?days=999", headers=tokens["admin"]).status_code == 422
    assert client.get(f"{API}/admin/usage/summary?days=0", headers=tokens["admin"]).status_code == 200
