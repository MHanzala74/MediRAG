"""
Usage-tracking + account/session SQL tested against a REAL PostgreSQL.

Uses a throw-away database (created and dropped by the test, never your dev DB).
Skipped automatically when PostgreSQL is not reachable with the POSTGRES_* settings.
"""
from datetime import datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from app.core.config import settings  # noqa: E402
from app.core.pricing import estimate_cost  # noqa: E402
from app.db import database, models, usage  # noqa: E402


def sql(query, params=()):
    conn = database.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
        return rows
    finally:
        database.release_connection(conn)


def test_migration_from_old_schema_is_idempotent(db):
    # a database created by the previous version: users without is_active/last_login
    sql("""CREATE TABLE users (id SERIAL PRIMARY KEY, username VARCHAR(100) UNIQUE NOT NULL,
           password TEXT NOT NULL, role VARCHAR(50) NOT NULL DEFAULT 'user', created_at TIMESTAMP DEFAULT NOW())""")
    sql("INSERT INTO users (username, password, role) VALUES ('legacy_user', 'x', 'user')")
    models.create_tables()
    models.create_tables()                                   # running twice must be harmless
    assert models.get_user("legacy_user")["is_active"] is True
    assert sql("SELECT to_regclass('usage_logs')")[0][0] == "usage_logs"


@pytest.fixture(scope="module")
def seeded(db):
    for name, role in [("alice_u", "user"), ("bob_u", "user"), ("carol_u", "user"), ("root_a", "admin")]:
        models.insert_user(name, "hash", role)
    ok = dict(status="ok", provider="openai", model="gpt-4o-mini-2024-07-18", retrieval_method="hybrid", sources_count=2)
    for i in range(3):
        usage.record_usage(username="alice_u", role="user", session_id="s-a", prompt_tokens=1000, completion_tokens=200,
                           latency_ms=1000 + i * 500, query_chars=40, answer_chars=300, **ok)
    usage.record_usage(username="alice_u", role="user", session_id="s-a", status="blocked", latency_ms=50)
    usage.record_usage(username="bob_u", role="user", session_id="s-b", status="error", latency_ms=10)
    usage.record_usage(username="root_a", role="admin", session_id="s-r", status="ok", provider="groq",
                       model="mystery-model", prompt_tokens=100, completion_tokens=50, latency_ms=800)
    # an old request (40 days ago) to test range filtering
    sql("""INSERT INTO usage_logs (created_at, username, role, session_id, status, total_tokens, prompt_tokens)
           VALUES (%s, 'bob_u', 'user', 's-old', 'ok', 999, 999)""", (datetime.now(timezone.utc) - timedelta(days=40),))


def test_summary_totals_and_users(seeded):
    s = usage.usage_summary(7)
    t = s["totals"]
    assert t["requests"] == 6 and t["active_users"] == 3                # alice, bob, root (old row excluded)
    assert (t["prompt_tokens"], t["completion_tokens"]) == (3100, 650)
    assert t["total_tokens"] == 3750
    assert t["blocked"] == 1 and t["errors"] == 1
    assert t["unpriced_requests"] == 1                                  # the 'mystery-model' request
    expected = 3 * estimate_cost("gpt-4o-mini", 1000, 200)
    assert abs(t["cost_usd"] - expected) < 1e-6
    assert s["users"] == {"regular": 4, "admins": 1, "disabled": 0, "total": 5}   # +legacy_user from migration test
    # ok-only latency average: (1000 + 1500 + 2000 + 800) / 4
    assert t["avg_latency_ms"] == 1325


def test_range_filtering_and_daily_series(seeded):
    assert usage.usage_summary(7)["totals"]["requests"] == 6
    assert usage.usage_summary(60)["totals"]["requests"] == 7
    assert usage.usage_summary(None)["totals"]["requests"] == 7
    daily = usage.usage_summary(7)["daily"]
    assert len(daily) == 7 and daily[-1]["requests"] == 6 and daily[0]["requests"] == 0
    assert daily[-1]["tokens"] == 3750 and daily[-1]["users"] == 3
    assert usage.usage_summary(None)["daily"][0]["requests"] == 1       # starts at the oldest day with data


def test_by_model(seeded):
    models_ = {m["model"]: m for m in usage.usage_summary(7)["by_model"]}
    assert models_["gpt-4o-mini-2024-07-18"]["requests"] == 3
    assert models_["mystery-model"]["cost_usd"] == 0
    assert models_["-"]["requests"] == 2                                # blocked + error rows have no model


def test_per_user_table_includes_idle_users(seeded):
    rows = usage.usage_by_user(7)
    by = {r["username"]: r for r in rows}
    assert rows[0]["username"] == "alice_u"                             # most active first
    assert by["alice_u"]["requests"] == 4 and by["alice_u"]["blocked"] == 1 and by["alice_u"]["total_tokens"] == 3600
    assert by["bob_u"]["requests"] == 1 and by["bob_u"]["errors"] == 1  # old bob request excluded
    assert by["carol_u"]["requests"] == 0 and by["carol_u"]["last_request"] is None
    assert by["root_a"]["role"] == "admin"
    all_time = {r["username"]: r for r in usage.usage_by_user(None)}
    assert all_time["bob_u"]["requests"] == 2                          # includes the 40-day-old request


def test_recent_requests(seeded):
    recent = usage.usage_recent(limit=3)
    assert len(recent) == 3 and recent[0]["created_at"] >= recent[1]["created_at"]
    only_alice = usage.usage_recent(limit=50, username="alice_u")
    assert len(only_alice) == 4 and {r["username"] for r in only_alice} == {"alice_u"}
    assert "query" not in only_alice[0] and "answer" not in only_alice[0]     # metadata only


def test_account_activation(seeded):
    assert models.get_user_account("carol_u") == {"username": "carol_u", "role": "user", "is_active": True}
    assert models.set_user_active("carol_u", False) is True
    assert models.get_user_account("carol_u")["is_active"] is False
    assert usage.usage_summary(7)["users"]["disabled"] == 1
    assert models.set_user_active("carol_u", True) is True
    assert models.set_user_active("nobody_x", False) is False
    assert models.get_user_account("nobody_x") is None


def test_last_login(seeded):
    assert {r["username"]: r for r in usage.usage_by_user(7)}["alice_u"]["last_login"] is None
    models.touch_last_login("alice_u")
    assert {r["username"]: r for r in usage.usage_by_user(7)}["alice_u"]["last_login"] is not None


def test_session_ownership(seeded):
    assert models.claim_session("sess-1", "alice_u", "user") is True
    assert models.claim_session("sess-1", "alice_u", "user") is True     # idempotent for the owner
    assert models.claim_session("sess-1", "bob_u", "user") is False      # someone else cannot take it over
    assert models.session_owner("sess-1") == "alice_u"
    assert models.session_owner("unknown") is None


def test_record_usage_never_raises(seeded, monkeypatch):
    monkeypatch.setattr(usage, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    usage.record_usage(username="alice_u", role="user", session_id="x", status="ok")   # must not raise


def test_cost_estimation(monkeypatch):
    assert estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75
    assert estimate_cost("gpt-4o-mini-2024-07-18", 1000, 0) == estimate_cost("gpt-4o-mini", 1000, 0)
    assert estimate_cost("gpt-4o-2024-08-06", 1_000_000, 0) == 2.5           # longest prefix: gpt-4o, not gpt-4o-mini
    assert estimate_cost("unknown-model", 10, 10) is None
    monkeypatch.setattr(settings, "llm_price_input_per_1m", 1.0)
    monkeypatch.setattr(settings, "llm_price_output_per_1m", 2.0)
    assert estimate_cost("unknown-model", 1_000_000, 1_000_000) == 3.0       # flat override wins
