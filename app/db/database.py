"""
PostgreSQL connection pool using psycopg2.
All DB operations go through get_connection().
"""
import psycopg2
from psycopg2 import pool
from app.core.config import settings
from app.utils.logger import logger

_pool: pool.SimpleConnectionPool | None = None


def init_pool():
    global _pool
    try:
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host=settings.postgres_host,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            port=settings.postgres_port,
            sslmode="require" if settings.is_production else "prefer",
        )
        logger.info("PostgreSQL connection pool initialized ✅")
    except Exception as e:
        logger.error("Failed to init DB pool: %s", e)
        raise


def get_connection():
    if _pool is None:
        init_pool()
    return _pool.getconn()


def release_connection(conn):
    if _pool:
        _pool.putconn(conn)


def close_pool():
    if _pool:
        _pool.closeall()
        logger.info("PostgreSQL pool closed")
