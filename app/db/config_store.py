"""Persistence for admin-managed configuration (app_config) and its audit trail (config_audit)."""
from app.db.database import get_connection, release_connection


def load_all() -> dict[str, dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value, is_secret, updated_by, updated_at FROM app_config")
        rows = cur.fetchall()
        cur.close()
        return {r[0]: {"value": r[1], "is_secret": r[2], "updated_by": r[3], "updated_at": r[4].isoformat()} for r in rows}
    finally:
        release_connection(conn)


def version() -> tuple:
    """Cheap change detector for other worker processes: (latest update, row count)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(updated_at)::text, ''), COUNT(*) FROM app_config")
        row = cur.fetchone()
        cur.close()
        return tuple(row)
    finally:
        release_connection(conn)


def save_changes(username: str, upserts: dict[str, tuple[str, bool]], deletes: list[str],
                 audit: list[tuple[str, str | None, str | None]]) -> None:
    """Apply every change and its audit rows in ONE transaction."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        for key, (value, is_secret) in upserts.items():
            cur.execute(
                """INSERT INTO app_config (key, value, is_secret, updated_by, updated_at) VALUES (%s, %s, %s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, is_secret = EXCLUDED.is_secret,
                                                   updated_by = EXCLUDED.updated_by, updated_at = NOW()""",
                (key, value, is_secret, username))
        for key in deletes:
            cur.execute("DELETE FROM app_config WHERE key = %s", (key,))
        for key, old, new in audit:
            cur.execute("INSERT INTO config_audit (username, key, old_value, new_value) VALUES (%s, %s, %s, %s)",
                        (username, key, old, new))
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def recent_audit(limit: int = 30) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT at, username, key, old_value, new_value FROM config_audit ORDER BY id DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
        cur.close()
        return [{"at": r[0].isoformat(), "username": r[1], "key": r[2], "old": r[3], "new": r[4]} for r in rows]
    finally:
        release_connection(conn)
