"""`cp .env.example .env` must always give a startable configuration."""
from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_loads_unchanged(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("LLM_PRICE", "POSTGRES", "PARSER", "ADMIN_SIGNUP", "DOCLING", "OPENAI", "GROQ")):
            monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=ROOT / ".env.example")
    assert s.parser == "auto" and s.llm_price_input_per_1m is None and s.admin_signup_code == ""


def test_blank_values_mean_default(tmp_path, monkeypatch):
    for k in ("LLM_PRICE_INPUT_PER_1M", "MAX_UPLOAD_MB", "DOCLING_MODELS_DIR"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("LLM_PRICE_INPUT_PER_1M=\nMAX_UPLOAD_MB=\nDOCLING_MODELS_DIR=\nOPENAI_API_KEY=\n")
    s = Settings(_env_file=env)
    assert s.llm_price_input_per_1m is None and s.max_upload_mb == 50 and s.docling_models_dir == ""
