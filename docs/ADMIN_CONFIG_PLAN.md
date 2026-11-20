# Admin model configuration — plan and status (WIP)

**Goal.** Admins configure the model (provider, model name, API keys, temperature, retrieval knobs, evaluation judge)
in the web UI instead of editing `.env`. Whatever the admin sets is what users' chats use, and users can see which model is active.

Branch: `feature/admin-config-wip` (nothing of this is on `main` yet).

## Design decisions (agreed, do not re-debate)
| Topic | Decision |
|---|---|
| Precedence | admin value (DB) > `.env` > code default. Each field can be reset to its `.env` value. |
| Editable | `llm_provider` (maps to `settings.primary_llm`), `openai_model`, `groq_model`, `llm_temperature`, `llm_max_tokens`, `openai_api_key` (secret), `groq_api_key` (secret), `openai_base_url`, `retriever_k`, `reranker_top_n` (≤ k), `use_hybrid_search`, `use_reranker`, `chat_history_limit`, `eval_judge_provider`, `eval_judge_model`, `parser` (`deep` only if Docling installed). |
| Read-only (shown, not editable) | embedding provider/model (changing them needs a full re-index), guardrails on/off (never switchable from the UI in a medical app), chunk size, DB/JWT settings. Show the *active* embedding provider, because the router silently falls back to HuggingFace when `EMBEDDING_PROVIDER=openai` has no key. |
| API keys | Stored **encrypted** (Fernet, key derived from `SETTINGS_ENCRYPTION_KEY` or `JWT_SECRET_KEY`) in table `app_config`; write-only: the API returns only `••••ab12`. Refuse to store keys in production while the JWT secret is the default. |
| Audit | Table `config_audit`: who changed what, secrets logged as "set"/"changed"/"removed" only. Show the last 30 in the UI. |
| Multi-worker | Each process re-checks `app_config` version (`max(updated_at), count`) at most every 5 s via `runtime_config.ensure_fresh()`. |
| Applying | `runtime_config.reload()` sets attributes on the global `settings` object. Only keys that have (or had) an override are touched, so test monkeypatches of other settings survive. Original (env/default) value is captured lazily the first time a key is overridden. |
| Users | `GET /api/v1/config/public` (any logged-in user): active provider + model (+ hybrid/reranker flags). Show it as a chip in the top bar / under the chat box. |
| Not configured yet | `get_llm()` raises `LLMNotConfigured`; chat endpoints answer **503** "The assistant is not configured yet. Please ask an administrator…". |

## Status
**Done (this branch)**
- `app/core/config.py`: new settings `llm_temperature`, `llm_max_tokens`, `openai_base_url`, `eval_judge_provider`, `eval_judge_model`, `settings_encryption_key`; `.env.example` has `SETTINGS_ENCRYPTION_KEY=`.
- `app/core/secrets.py`: `encrypt/decrypt/mask/can_store_secrets`.
- `app/db/models.py`: tables `app_config`, `config_audit` (created in `create_tables`).
- `app/db/config_store.py`: `load_all`, `version`, `save_changes` (one transaction, with audit), `recent_audit`.
- `tests/fake_openai_server.py`: in-thread OpenAI-compatible server (`FakeOpenAIServer().start()`; `.url`, `.requests`, `.expected_key`, `.reset()`, `.stop()`). Streams, reports usage 120 in / 12 out, echoes `[Source: …]` tags, 401 on a wrong key, `/v1/models`, `/v1/embeddings`.
- Verified with the real SDK: **`ChatOpenAI(api_key=callable)` and `OpenAIEmbeddings(api_key=callable)` re-read the key on every call** → key rotation needs no rebuild.
- Existing suite still green (89 passed).

**To do (suggested commits, in order)**
1. `app/core/runtime_config.py` — field specs + validation (`ConfigError` → 422 with per-field messages), `reload()`, `ensure_fresh(ttl=5)`, `update(username, changes)` (`None` = reset to env), `describe()` (value, source admin/env/default, `secret_set`, `secret_hint`, updated_by/at, choices, min/max), listeners. Unit tests on real PostgreSQL (`tests/conftest.py` gives a throw-away DB): precedence, validation, encryption (plaintext never in DB), masking, audit, reset, version-based refresh, production safeguard.
2. Wire it in: `app/rag/llm_router.py` (`effective_llm()` → frozen dataclass; `lru_cache`d builder keyed by it; `api_key=lambda: settings…`; `base_url`; temperature; max_tokens; `LLMNotConfigured`), `evaluation/judge.py` (read `settings.eval_judge_*`, call `ensure_fresh`), `app/api/v1/health.py` (effective provider), `app/guardrails/nemo_guardrails.py` (NeMo reads `OPENAI_API_KEY` from the environment → set `os.environ` in `_load`; add `reset()` and call it when the key changes), `app/rag/embeddings/*` (callable key, `active_embedding_info()`), `main.py` lifespan (`runtime_config.reload()` **before** `get_vectorstore()`), `chat.py` (503 mapping, both endpoints), call `ensure_fresh()` at the start of `prepare_rag`.
3. API: `GET/PATCH /admin/config`, `POST /admin/config/test-llm` (tiny completion with candidate values; sanitised error, never echo keys), `POST /admin/config/models` (list models from the provider, filter embeddings/whisper/tts…), `GET /config/public`. Tests with the real SDK against `FakeOpenAIServer` (Authorization header equals the admin-set key, model name and temperature in the request body, wrong key → clean error).
4. UI: admin **Settings** tab (cards: Language model, Retrieval, Evaluation judge, Parsing, Fixed-at-deploy read-only, Change log; "Test connection", "Load models", per-field reset, masked key fields with Replace/Remove) + model chip for every user. jsdom tests: stub server and the real app. For the real-app UI test (`tests/ui/real_backend_server.py`) stop patching `get_llm` and run `FakeOpenAIServer`; configure it **through the UI**.
5. `tests_e2e`: admin sets key + base URL + model through the API → user chat/stream uses them (`model_used`, usage from the server's usage chunk) → admin switches model → next chat uses it → removing the key gives the friendly 503. Then a short README/docs section.

## Gotchas found while reading the code
- `get_embedding_model()` silently falls back to HuggingFace if `EMBEDDING_PROVIDER=openai` and no key → never let a runtime key change flip the embedding model; embeddings are decided once (startup).
- `get_llm()` currently resolves legacy-style: `primary_llm=openai` without an OpenAI key but with a Groq key silently uses Groq. Keep it, but surface "selected X, using Y" in the panel.
- The evaluation API imports DeepEval at start-up (it is a hard dependency).
- Dev sandbox facts: no access to api.openai.com / huggingface.co; PostgreSQL there dies between tool calls (`pg_ctlcluster 16 main start`); run tests with `POSTGRES_PASSWORD=postgres`.
