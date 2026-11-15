"""
Database schema creation and user CRUD operations.
"""
from app.db.database import get_connection, release_connection
from app.utils.logger import logger


def create_tables():
    """Create all required tables if they do not exist."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          SERIAL PRIMARY KEY,
                username    VARCHAR(100) UNIQUE NOT NULL,
                password    TEXT NOT NULL,
                role        VARCHAR(50)  NOT NULL DEFAULT 'user',
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id          SERIAL PRIMARY KEY,
                username    VARCHAR(100) NOT NULL,
                session_id  VARCHAR(100) NOT NULL,
                role        VARCHAR(50)  NOT NULL,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id          SERIAL PRIMARY KEY,
                session_id  VARCHAR(100) NOT NULL,
                role        VARCHAR(20)  NOT NULL,  -- 'user' | 'assistant'
                content     TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_docs (
                id          SERIAL PRIMARY KEY,
                doc_id      VARCHAR(100) UNIQUE NOT NULL,
                filename    TEXT NOT NULL,
                role        VARCHAR(50)  NOT NULL,
                uploaded_by VARCHAR(100) NOT NULL,
                chunk_count INT DEFAULT 0,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)
        # ── migrations for existing databases ─────────────────────────
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active  BOOLEAN NOT NULL DEFAULT TRUE;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMPTZ;")
        # a chat session belongs to exactly one user (see claim_session)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_chat_sessions_session_id ON chat_sessions(session_id);")
        # ── per-request usage log (metadata only — never the question/answer text) ──
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id                BIGSERIAL PRIMARY KEY,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                username          VARCHAR(100) NOT NULL,
                role              VARCHAR(50),
                session_id        VARCHAR(100),
                status            VARCHAR(20) NOT NULL,      -- ok | blocked | error | cancelled
                provider          VARCHAR(30),
                model             VARCHAR(100),
                prompt_tokens     INT NOT NULL DEFAULT 0,
                completion_tokens INT NOT NULL DEFAULT 0,
                total_tokens      INT NOT NULL DEFAULT 0,
                tokens_estimated  BOOLEAN NOT NULL DEFAULT FALSE,
                cost_usd          NUMERIC(12,6),             -- estimate at request time; NULL = unknown model
                latency_ms        INT,
                retrieval_method  VARCHAR(20),
                sources_count     INT NOT NULL DEFAULT 0,
                query_chars       INT NOT NULL DEFAULT 0,
                answer_chars      INT NOT NULL DEFAULT 0
            );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_usage_created  ON usage_logs(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_usage_user_time ON usage_logs(username, created_at);")
        conn.commit()
        cur.close()
        logger.info("DB tables verified/created ✅")
    except Exception as e:
        conn.rollback()
        logger.error("Table creation failed: %s", e)
        raise
    finally:
        release_connection(conn)


# ── User CRUD ─────────────────────────────────────────────────────────

def get_user(username: str) -> dict | None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT username, password, role, is_active FROM users WHERE username = %s",
            (username,)
        )
        row = cur.fetchone()
        cur.close()
        return {"username": row[0], "password": row[1], "role": row[2], "is_active": row[3]} if row else None
    finally:
        release_connection(conn)


def insert_user(username: str, hashed_pw: str, role: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (username, hashed_pw, role)
        )
        conn.commit()
        cur.close()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_user_account(username: str) -> dict | None:
    """Current role + active flag straight from the DB (used on every authenticated request)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, role, is_active FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        cur.close()
        return {"username": row[0], "role": row[1], "is_active": row[2]} if row else None
    finally:
        release_connection(conn)


def touch_last_login(username: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET last_login = NOW() WHERE username = %s", (username,))
        conn.commit()
        cur.close()
    finally:
        release_connection(conn)


def set_user_active(username: str, active: bool) -> bool:
    """Enable / disable an account. Returns False if the user does not exist."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_active = %s WHERE username = %s", (active, username))
        updated = cur.rowcount > 0
        conn.commit()
        cur.close()
        return updated
    finally:
        release_connection(conn)


# ── Chat session ownership ────────────────────────────────────────────

def claim_session(session_id: str, username: str, role: str) -> bool:
    """Bind a session to its first user. True if `username` owns it, False if someone else does."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_sessions (username, session_id, role) VALUES (%s, %s, %s) "
            "ON CONFLICT (session_id) DO NOTHING",
            (username, session_id, role),
        )
        cur.execute("SELECT username FROM chat_sessions WHERE session_id = %s", (session_id,))
        owner = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return owner == username
    finally:
        release_connection(conn)


def session_owner(session_id: str) -> str | None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT username FROM chat_sessions WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        release_connection(conn)


# ── Chat history CRUD ─────────────────────────────────────────────────

def save_message(session_id: str, role: str, content: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_history (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content)
        )
        conn.commit()
        cur.close()
    finally:
        release_connection(conn)


def get_history(session_id: str, limit: int = 10) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT role, content FROM chat_history
               WHERE session_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (session_id, limit)
        )
        rows = cur.fetchall()
        cur.close()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    finally:
        release_connection(conn)


# ── Doc metadata CRUD ─────────────────────────────────────────────────

def save_doc_metadata(doc_id: str, filename: str, role: str, uploaded_by: str, chunk_count: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO uploaded_docs (doc_id, filename, role, uploaded_by, chunk_count)
               VALUES (%s, %s, %s, %s, %s)""",
            (doc_id, filename, role, uploaded_by, chunk_count)
        )
        conn.commit()
        cur.close()
    finally:
        release_connection(conn)


def list_docs(role: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        if role:
            cur.execute(
                "SELECT doc_id, filename, role, uploaded_by, chunk_count, created_at FROM uploaded_docs WHERE role = %s ORDER BY created_at DESC",
                (role,)
            )
        else:
            cur.execute(
                "SELECT doc_id, filename, role, uploaded_by, chunk_count, created_at FROM uploaded_docs ORDER BY created_at DESC"
            )
        rows = cur.fetchall()
        cur.close()
        return [
            {"doc_id": r[0], "filename": r[1], "role": r[2],
             "uploaded_by": r[3], "chunk_count": r[4], "created_at": str(r[5])}
            for r in rows
        ]
    finally:
        release_connection(conn)
