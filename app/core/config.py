"""
Centralized settings — all values from environment variables.
"""
from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────
    app_name: str    = "RAG Medical Assistant"
    app_version: str = "2.0.0"
    app_env: Literal["development", "production"] = "development"
    api_prefix: str  = "/api/v1"
    host: str        = "0.0.0.0"
    port: int        = 8000

    # ── Security / JWT ────────────────────────────────────────────────
    jwt_secret_key: str     = "change-me-in-production"
    jwt_algorithm: str      = "HS256"
    jwt_expire_minutes: int = 60
    # Admin accounts can upload documents and run (paid) evaluations. If set, admin
    # signup requires this code. If empty: allowed in development, refused in production.
    admin_signup_code: str  = ""

    # ── LLM ──────────────────────────────────────────────────────────
    openai_api_key: str  = ""
    groq_api_key: str    = ""
    # Optional flat price override (USD per 1M tokens) used for the dashboard's cost ESTIMATE.
    llm_price_input_per_1m:  float | None = None
    llm_price_output_per_1m: float | None = None
    primary_llm: Literal["openai", "groq"] = "openai"
    openai_model: str    = "gpt-4o-mini"
    groq_model: str      = "llama-3.3-70b-versatile"

    # ── Embeddings ────────────────────────────────────────────────────
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_provider: Literal["openai", "huggingface"] = "openai"
    hf_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ── RAG ───────────────────────────────────────────────────────────
    chroma_dir: str        = "./chroma_db"
    docs_upload_dir: str   = "./docs_store"
    chunk_size: int        = 600
    chunk_overlap: int     = 120
    table_chunk_size: int  = 1800          # tables get more room; split by rows (header repeated) beyond this
    max_upload_mb: int     = 50
    # Document parsing: fast = pypdf (text PDFs only) · deep = Docling (layout, tables, OCR, DOCX/PPTX/HTML/images)
    #                   auto = pypdf first, Docling when the PDF looks scanned (needs Docling installed)
    parser: Literal["auto", "fast", "deep"] = "auto"
    parser_scanned_min_chars: int = 40     # avg extractable chars/page below which a PDF counts as scanned
    docling_ocr: bool      = True
    docling_tables: bool   = True
    docling_models_dir: str = ""           # offline model folder (see scripts/download_docling_models.py)
    docling_timeout_s: int = 900           # per-document time limit
    retriever_k: int       = 6          # initial retrieval count
    reranker_top_n: int    = 3          # after reranking
    use_hybrid_search: bool = True
    use_reranker: bool      = True
    chat_history_limit: int = 10        # turns to keep in memory

    # ── Database ──────────────────────────────────────────────────────
    postgres_host: str     = "localhost"
    postgres_port: int     = 5432
    postgres_db: str       = "medical_rag"
    postgres_user: str     = "postgres"
    postgres_password: str = ""

    # ── Guardrails ────────────────────────────────────────────────────
    max_query_length: int  = 1000
    enable_guardrails: bool = True

    # ── Logging ──────────────────────────────────────────────────────
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,          # `KEY=` (blank) in .env means "use the default", never a parse error
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
