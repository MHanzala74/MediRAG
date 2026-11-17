"""Whole-application tests live apart from tests/ because they import the REAL `main.app`
(tests/ replaces some modules with stubs). Run:  pytest tests_e2e"""
from tests.conftest import db  # noqa: F401  (throw-away PostgreSQL database fixture)
