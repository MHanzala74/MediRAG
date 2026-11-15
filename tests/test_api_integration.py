"""
HTTP-level integration test: REAL auth, REAL JWT/security, REAL admin router, REAL PostgreSQL.
Only the RAG chain and document ingestion are stubbed (they need an LLM and embedding models).
"""
import asyncio
import json
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


class FakePrepared:
    def __init__(self, blocked_result=None):
        self.inputs, self.method, self.n_docs, self.prompt_chars = {}, "hybrid", 2, 400
        self.sources = [{"filename": "a.pdf", "doc_id": "d", "chunk_preview": "p"}]
        self.blocked_result = blocked_result


async def fake_prepare(query, user_role, session_id):
    mode = CHAIN_BEHAVIOUR["mode"]
    if mode == "blocked":
        raise GuardrailException("I can only assist with medical questions.")
    if mode == "prepare_error":
        raise RAGException(RuntimeError("vector store down"))
    if mode == "context_blocked":
        return FakePrepared({"answer": "No relevant information found.", "sources": [], "model_used": "none",
                             "retrieval_method": "hybrid", "guardrail_passed": False})
    return FakePrepared()


async def fake_stream(query, session_id, prepared):
    mode = CHAIN_BEHAVIOUR["mode"]
    for w in ["Diabetes ", "causes ", "thirst."]:
        await asyncio.sleep(0.01)
        yield {"type": "token", "text": w}
    if mode == "stream_error":
        raise RAGException(RuntimeError("provider dropped the connection"))
    answer = "Diabetes causes thirst."
    if mode == "replace":
        yield {"type": "replace", "text": "WARNING\n\n" + answer + " [disclaimer]"}
        answer = "WARNING\n\n" + answer + " [disclaimer]"
    else:
        yield {"type": "token", "text": " [disclaimer]"}
        answer += " [disclaimer]"
    yield {"type": "done", "result": {
        "answer": answer, "sources": prepared.sources, "model_used": "gpt-4o-mini-2024-07-18",
        "retrieval_method": "hybrid", "guardrail_passed": True, "provider": "openai",
        "usage": {"prompt_tokens": 500, "completion_tokens": 40, "tokens_estimated": False}}}


chain_mod = types.ModuleType("app.rag.chain")
chain_mod.run_rag_chain = fake_chain
chain_mod.prepare_rag = fake_prepare
chain_mod.stream_rag = fake_stream
chain_mod.llm_model_name = lambda: "gpt-4o-mini-2024-07-18"
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


# ─────────────────────────── streaming (/chat/stream) ───────────────────────────
def sse_events(response):
    """Parse a text/event-stream body into [(event, data), ...]."""
    out = []
    for frame in response.text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in frame.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def stream(c, who, session, msg="what is diabetes?"):
    return c.post(f"{API}/chat/stream", json={"message": msg, "session_id": session}, headers=who)


def user_row(client, admin, name):
    rows = client.get(f"{API}/admin/usage/users", headers=admin).json()["users"]
    return next(r for r in rows if r["username"] == name)


@pytest.fixture(scope="module")
def streamer(client):
    return signup_login(client, "stream_user")


def test_stream_happy_path(client, tokens, streamer):
    CHAIN_BEHAVIOUR["mode"] = "ok"
    r = stream(client, streamer, "sess-stream-1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    ev = sse_events(r)
    kinds = [k for k, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done" and kinds.count("token") == 4        # 3 words + disclaimer
    assert ev[0][1]["sources"][0]["filename"] == "a.pdf" and ev[0][1]["model_used"].startswith("gpt-4o-mini")
    streamed = "".join(d["text"] for k, d in ev if k == "token")
    done = ev[-1][1]
    assert streamed == done["answer"] == "Diabetes causes thirst. [disclaimer]"             # stream == saved answer
    assert done["guardrail_passed"] is True and done["first_token_ms"] is not None
    row = user_row(client, tokens["admin"], "stream_user")
    assert row["requests"] == 1 and row["prompt_tokens"] == 500 and row["completion_tokens"] == 40


def test_stream_replace_event(client, streamer):
    CHAIN_BEHAVIOUR["mode"] = "replace"
    ev = sse_events(stream(client, streamer, "sess-stream-2"))
    replaces = [d for k, d in ev if k == "replace"]
    assert len(replaces) == 1 and replaces[0]["text"].startswith("WARNING")
    assert ev[-1][1]["answer"] == replaces[0]["text"]


def test_stream_requires_auth_and_ownership(client, tokens, streamer):
    CHAIN_BEHAVIOUR["mode"] = "ok"
    assert client.post(f"{API}/chat/stream", json={"message": "hi", "session_id": "s"}).status_code == 401
    assert stream(client, tokens["alice"], "sess-stream-1").status_code == 403           # someone else's session
    assert stream(client, streamer, "x" * 101).status_code == 422


def test_stream_input_guardrail_is_a_plain_422(client, streamer):
    CHAIN_BEHAVIOUR["mode"] = "blocked"
    r = stream(client, streamer, "sess-stream-3", "write python")
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")
    assert "medical" in r.json()["detail"]


def test_stream_prepare_failure_is_a_plain_500(client, streamer):
    CHAIN_BEHAVIOUR["mode"] = "prepare_error"
    assert stream(client, streamer, "sess-stream-4").status_code == 500


def test_stream_context_rail_refusal(client, tokens, streamer):
    CHAIN_BEHAVIOUR["mode"] = "context_blocked"
    ev = sse_events(stream(client, streamer, "sess-stream-5", "rare tropical disease"))
    assert [k for k, _ in ev] == ["meta", "token", "done"]
    assert ev[1][1]["text"] == "No relevant information found." and ev[-1][1]["guardrail_passed"] is False
    assert user_row(client, tokens["admin"], "stream_user")["blocked"] >= 2            # input-blocked + context-blocked


def test_stream_error_after_first_token(client, tokens, streamer):
    CHAIN_BEHAVIOUR["mode"] = "stream_error"
    ev = sse_events(stream(client, streamer, "sess-stream-6"))
    kinds = [k for k, _ in ev]
    assert kinds[:4] == ["meta", "token", "token", "token"] and kinds[-1] == "error"
    assert "provider dropped" in ev[-1][1]["detail"]
    assert user_row(client, tokens["admin"], "stream_user")["errors"] >= 1


def test_client_stop_is_logged_as_cancelled(client, tokens, streamer, db):
    """Simulate the client disconnecting after the first token: the generator is closed."""
    from app.api.v1 import chat as chat_mod
    from app.schemas.chat import ChatRequest

    CHAIN_BEHAVIOUR["mode"] = "ok"
    before = client.get(f"{API}/admin/usage/summary?days=7", headers=tokens["admin"]).json()["totals"]

    async def run():
        req = ChatRequest(message="what is diabetes?", session_id="sess-stream-7")
        assert models.claim_session(req.session_id, "stream_user", "user")
        gen = chat_mod._sse_events(req, {"username": "stream_user", "role": "user"}, FakePrepared(), time.perf_counter())
        frames = [await gen.__anext__(), await gen.__anext__()]           # meta + first token
        await gen.aclose()                                                # ← disconnect
        return frames

    import time
    frames = asyncio.run(run())
    assert frames[0].startswith("event: meta") and frames[1].startswith("event: token")
    after = client.get(f"{API}/admin/usage/summary?days=7", headers=tokens["admin"]).json()["totals"]
    assert after["cancelled"] == before["cancelled"] + 1
    assert after["completion_tokens"] > before["completion_tokens"]       # the partial answer is still counted
    assert after["cost_usd"] > before["cost_usd"]
    recent = client.get(f"{API}/admin/usage/recent?limit=1&username=stream_user", headers=tokens["admin"]).json()["requests"]
    assert recent[0]["status"] == "cancelled"
