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
            "SELECT username, password, role FROM users WHERE username = %s",
            (username,)
        )
        row = cur.fetchone()
        cur.close()
        return {"username": row[0], "password": row[1], "role": row[2]} if row else None
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
