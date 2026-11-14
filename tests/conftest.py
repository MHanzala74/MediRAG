"""Shared fixtures: a throw-away PostgreSQL database per test module (never your dev DB)."""
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from app.core.config import settings  # noqa: E402
from app.db import database  # noqa: E402


@pytest.fixture(scope="module")
def db():
    """Create an empty database, point the app's pool at it, drop it afterwards.
    Skips when PostgreSQL is unreachable (configure with POSTGRES_* env vars)."""
    try:
        admin = psycopg2.connect(host=settings.postgres_host, port=settings.postgres_port,
                                 user=settings.postgres_user, password=settings.postgres_password,
                                 dbname="postgres", connect_timeout=3)
    except Exception as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    admin.autocommit = True
    name = "medirag_test_" + uuid.uuid4().hex[:8]
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    original = settings.postgres_db
    settings.postgres_db = name
    database._pool = None
    database.init_pool()
    yield name
    database.close_pool()
    database._pool = None
    settings.postgres_db = original
    admin.cursor().execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    admin.close()
