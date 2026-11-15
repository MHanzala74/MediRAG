"""
Per-request usage logging + the aggregate queries behind the admin dashboard.

Only metadata is stored (who, when, tokens, latency, status) — never the question or
answer text, which is medical data and already lives in chat_history under the user's session.
"""
from datetime import date, datetime, timedelta, timezone

from app.core.pricing import estimate_cost
from app.db.database import get_connection, release_connection
from app.utils.logger import logger


def _f(v) -> float:
    return float(v) if v is not None else 0.0


def _since(days: int | None):
    """days=None/0 → all time."""
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


def record_usage(
    *, username: str, role: str, session_id: str, status: str,
    provider: str | None = None, model: str | None = None,
    prompt_tokens: int = 0, completion_tokens: int = 0, tokens_estimated: bool = False,
    latency_ms: int | None = None, retrieval_method: str | None = None,
    sources_count: int = 0, query_chars: int = 0, answer_chars: int = 0,
) -> None:
    """Insert one usage row. Never raises — analytics must not break the chat."""
    conn = None
    try:
        cost = estimate_cost(model, prompt_tokens, completion_tokens) if status in ("ok", "cancelled") else 0.0
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO usage_logs
               (username, role, session_id, status, provider, model, prompt_tokens, completion_tokens,
                total_tokens, tokens_estimated, cost_usd, latency_ms, retrieval_method,
                sources_count, query_chars, answer_chars)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (username, role, session_id, status, provider, model, prompt_tokens, completion_tokens,
             prompt_tokens + completion_tokens, tokens_estimated, cost, latency_ms, retrieval_method,
             sources_count, query_chars, answer_chars),
        )
        conn.commit()
        cur.close()
    except Exception as e:                                    # pragma: no cover - defensive
        logger.error("Could not record usage: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            release_connection(conn)


def _where(since):
    return ("WHERE created_at >= %s", (since,)) if since else ("", ())


def usage_summary(days: int | None) -> dict:
    since = _since(days)
    where, params = _where(since)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT COUNT(*), COUNT(DISTINCT username),
                       COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0),
                       COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost_usd),0),
                       COALESCE(AVG(latency_ms) FILTER (WHERE status='ok'),0),
                       COUNT(*) FILTER (WHERE status='blocked'), COUNT(*) FILTER (WHERE status='error'),
                       COUNT(*) FILTER (WHERE status='ok' AND cost_usd IS NULL),
                       COUNT(*) FILTER (WHERE status='cancelled')
                FROM usage_logs {where}""", params)
        r = cur.fetchone()
        totals = {
            "requests": r[0], "active_users": r[1], "prompt_tokens": int(r[2]),
            "completion_tokens": int(r[3]), "total_tokens": int(r[4]), "cost_usd": round(_f(r[5]), 6),
            "avg_latency_ms": round(_f(r[6])), "blocked": r[7], "errors": r[8], "unpriced_requests": r[9],
            "cancelled": r[10],
        }

        cur.execute("""SELECT COUNT(*) FILTER (WHERE role='user'), COUNT(*) FILTER (WHERE role='admin'),
                              COUNT(*) FILTER (WHERE NOT is_active), COUNT(*) FROM users""")
        u = cur.fetchone()
        users = {"regular": u[0], "admins": u[1], "disabled": u[2], "total": u[3]}

        cur.execute(
            f"""SELECT (created_at AT TIME ZONE 'UTC')::date AS d, COUNT(*), COALESCE(SUM(total_tokens),0),
                       COUNT(DISTINCT username)
                FROM usage_logs {where} GROUP BY d ORDER BY d""", params)
        by_day = {row[0]: {"requests": row[1], "tokens": int(row[2]), "users": row[3]} for row in cur.fetchall()}

        cur.execute(
            f"""SELECT COALESCE(provider,'-'), COALESCE(model,'-'), COUNT(*), COALESCE(SUM(prompt_tokens),0),
                       COALESCE(SUM(completion_tokens),0), COALESCE(SUM(total_tokens),0), COALESCE(SUM(cost_usd),0)
                FROM usage_logs {where} GROUP BY 1, 2 ORDER BY 3 DESC""", params)
        by_model = [{"provider": m[0], "model": m[1], "requests": m[2], "prompt_tokens": int(m[3]),
                     "completion_tokens": int(m[4]), "total_tokens": int(m[5]), "cost_usd": round(_f(m[6]), 6)}
                    for m in cur.fetchall()]
        cur.close()
    finally:
        release_connection(conn)

    # fill empty days so the chart has a continuous x-axis
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days - 1)) if days else (min(by_day) if by_day else today)
    start = max(start, today - timedelta(days=364))
    daily, d = [], start
    while d <= today:
        row = by_day.get(d, {"requests": 0, "tokens": 0, "users": 0})
        daily.append({"date": d.isoformat(), **row})
        d += timedelta(days=1)

    return {"days": days or 0, "totals": totals, "users": users, "daily": daily, "by_model": by_model}


def usage_by_user(days: int | None) -> list[dict]:
    """Every registered user (even with 0 requests) with their usage in the range."""
    where, params = _where(_since(days))
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT u.username, u.role, u.is_active, u.created_at, u.last_login,
                       COALESCE(a.requests,0), COALESCE(a.blocked,0), COALESCE(a.errors,0),
                       COALESCE(a.pt,0), COALESCE(a.ct,0), COALESCE(a.tt,0), COALESCE(a.cost,0),
                       COALESCE(a.lat,0), a.last_request
                FROM users u
                LEFT JOIN (
                    SELECT username, COUNT(*) AS requests,
                           COUNT(*) FILTER (WHERE status='blocked') AS blocked,
                           COUNT(*) FILTER (WHERE status='error')   AS errors,
                           SUM(prompt_tokens) AS pt, SUM(completion_tokens) AS ct, SUM(total_tokens) AS tt,
                           SUM(cost_usd) AS cost,
                           AVG(latency_ms) FILTER (WHERE status='ok') AS lat,
                           MAX(created_at) AS last_request
                    FROM usage_logs {where} GROUP BY username
                ) a ON a.username = u.username
                ORDER BY COALESCE(a.requests,0) DESC, u.username""", params)
        rows = cur.fetchall()
        cur.close()
    finally:
        release_connection(conn)
    iso = lambda v: v.isoformat() if v else None
    return [{
        "username": r[0], "role": r[1], "is_active": r[2], "created_at": iso(r[3]), "last_login": iso(r[4]),
        "requests": r[5], "blocked": r[6], "errors": r[7], "prompt_tokens": int(r[8]),
        "completion_tokens": int(r[9]), "total_tokens": int(r[10]), "cost_usd": round(_f(r[11]), 6),
        "avg_latency_ms": round(_f(r[12])), "last_request": iso(r[13]),
    } for r in rows]


def usage_recent(limit: int = 50, username: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql = """SELECT created_at, username, status, COALESCE(model,'-'), total_tokens, latency_ms, cost_usd,
                        retrieval_method, sources_count
                 FROM usage_logs"""
        params: tuple = ()
        if username:
            sql += " WHERE username = %s"
            params = (username,)
        cur.execute(sql + " ORDER BY created_at DESC LIMIT %s", params + (limit,))
        rows = cur.fetchall()
        cur.close()
    finally:
        release_connection(conn)
    return [{"created_at": r[0].isoformat(), "username": r[1], "status": r[2], "model": r[3],
             "total_tokens": r[4], "latency_ms": r[5], "cost_usd": None if r[6] is None else round(_f(r[6]), 6),
             "retrieval_method": r[7], "sources_count": r[8]} for r in rows]
