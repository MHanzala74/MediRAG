<div align="center">

# 🏥 RAG Medical Assistant

### Production-Grade Medical RAG · Multi-Model · Hybrid Search · Guardrails

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![LangChain](https://img.shields.io/badge/LangChain-0.3-orange)](https://langchain.com)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

> Upload medical PDFs → Ask questions → Get cited, safe, role-restricted answers.
> Built for production with multi-model LLM, hybrid search, reranking, and 4-layer medical guardrails.

</div>

---

## 📋 Table of Contents
- [Overview](#-overview)
- [Architecture](#-architecture)
- [Key Features](#-key-features)
- [API Endpoints](#-api-endpoints)
- [Quick Start](#-quick-start)
- [Document parsing (Docling)](#-document-parsing)
- [How to Use MediRAG (end-to-end)](#-how-to-use-medirag-end-to-end)
- [Docker Deployment](#-docker-deployment)
- [Environment Variables](#-environment-variables)
- [Guardrails System](#-guardrails-system)
- [RAG Evaluation (DeepEval)](#-rag-evaluation-deepeval)
- [Project Structure](#-project-structure)

---

## 🔍 Overview

RAG Medical Assistant is a production-ready Retrieval-Augmented Generation system designed for medical knowledge bases. It allows organizations to upload medical PDFs and enable role-restricted Q&A with full source citation.

---

## 🏗️ Architecture

![RAG Medical Assistant Architecture](docs/architecture.svg)

*Figure 1 — (a) offline ingestion, (b) guarded hybrid-RAG inference, (c) DeepEval evaluation. Also available as [PNG](docs/architecture.png) and [PDF](docs/architecture.pdf) for papers/slides; regenerate with `python docs/generate_architecture.py`.*

---

## ✨ Key Features

| Feature | Details |
|---|---|
| 🤖 Two LLM providers | OpenAI gpt-4o-mini **or** Groq llama-3.3-70b, chosen at startup by `PRIMARY_LLM` (Groq is used when no OpenAI key is set); no automatic runtime failover |
| 📄 Deep document parsing | Text PDFs via pypdf; optional **Docling** adds layout analysis, table structure, OCR for scans/images and DOCX / PPTX / HTML / Markdown. Citations carry **page numbers** |
| ⚡ Streaming answers | The chat shows the answer word by word as it is generated (Server-Sent Events), with a **Stop** button |
| 📊 Usage dashboard | Admin sees users, requests per user, tokens and estimated LLM cost; can disable accounts |
| 🔍 Hybrid Search | Semantic (ChromaDB) + BM25 keyword + RRF fusion |
| 🎯 Reranking | Cross-encoder ms-marco-MiniLM — reduces hallucination |
| 🛡️ 4-Layer Guardrails | Input · Context · Output · Hallucination detection |
| 🔐 JWT Auth | OAuth2 Bearer tokens, role-based access (user/admin) |
| 💬 Chat History | Multi-turn memory per session stored in PostgreSQL |
| 📄 Role-based Docs | Documents tagged per role — users only see their docs |
| 🌍 Multi-embedding | OpenAI embeddings or HuggingFace (free, local) |
| 📏 RAG Evaluation | 14 DeepEval metrics — retrieval, generation, safety — with CI gate |
| 🐳 Docker-ready | Full stack: API + PostgreSQL in one `docker compose up` |

---

## 🌐 API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `GET` | `/ui/` | — | **Web UI** (chat · documents · evaluation dashboard) |
| `GET` | `/` | — | API info (browsers are redirected to the UI) |
| `GET` | `/api/v1/health` | — | Health + feature flags |
| `POST` | `/api/v1/auth/signup` | — | Register user |
| `POST` | `/api/v1/auth/login` | — | Login → JWT token |
| `POST` | `/api/v1/documents/upload` | Admin | Upload a document — parsed in the background (`202`); `?wait=true` blocks until done |
| `GET` | `/api/v1/documents/{doc_id}` | Admin | Status of one document (`processing` / `ready` / `failed`) |
| `DELETE` | `/api/v1/documents/{doc_id}` | Admin | Delete a document and all its indexed chunks |
| `GET` | `/api/v1/documents/capabilities` | Admin | Supported file types, parsing modes, Docling installed? |
| `GET` | `/api/v1/documents/list` | Admin | List all documents |
| `POST` | `/api/v1/chat/query` | User | Ask a medical question (complete answer in one JSON response) |
| `POST` | `/api/v1/chat/stream` | User | Same, but streamed token by token (`text/event-stream`) — used by the web UI |
| `GET` | `/api/v1/chat/history/{session_id}` | User | Get chat history |
| `GET` | `/api/v1/admin/usage/summary?days=7` | Admin | KPIs, requests per day, per-model totals |
| `GET` | `/api/v1/admin/usage/users?days=7` | Admin | Every user: requests, tokens, est. cost, last login |
| `GET` | `/api/v1/admin/usage/recent` | Admin | Latest requests (metadata only) |
| `PATCH` | `/api/v1/admin/users/{username}/active` | Admin | Enable / disable an account |
| `GET` | `/api/v1/evaluation/options` | Admin | Metrics, datasets, judge status |
| `POST` | `/api/v1/evaluation/run` | Admin | Start a DeepEval run (background job) |
| `POST` | `/api/v1/evaluation/datasets/synthesize` | Admin | Generate a test set from indexed documents |
| `GET` | `/api/v1/evaluation/jobs/{id}` | Admin | Poll job progress |
| `GET` | `/api/v1/evaluation/reports` · `/reports/{id}` | Admin | List / fetch results |
| `GET` | `/api/v1/evaluation/reports/{id}/download?format=md\|csv\|json` | Admin | Download a report |
| `DELETE` | `/api/v1/evaluation/reports/{id}` | Admin | Delete a report |

---

## ⚡ Quick Start

### Prerequisites
- **Git** and **[uv](https://docs.astral.sh/uv/)** (`pip install uv`, or the installer from the uv docs). uv also downloads **Python 3.13** for you (see `.python-version`).
- **PostgreSQL 14+** — or Docker, to run it in one line (below).
- An LLM key: [OpenAI](https://platform.openai.com/api-keys) or [Groq](https://console.groq.com).

```bash
# 1 · get the code
git clone https://github.com/MHanzala74/MediRAG.git
cd MediRAG

# 2 · create the environment from the lock file  (first run downloads several GB: PyTorch, models' libraries)
uv sync --group dev                 # app + test tools
# uv sync --group dev --extra docling   # + deep document parsing (scans, tables, DOCX/PPTX/images) — optional, heavy

# 3 · PostgreSQL (skip if you already have one)
docker run -d --name medirag-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=medical_rag -p 5432:5432 postgres:16

# 4 · configuration
cp .env.example .env                # Windows PowerShell:  Copy-Item .env.example .env
uv run python -c "import secrets; print(secrets.token_hex(32))"      # → paste as JWT_SECRET_KEY
```

Edit `.env` — the values you must set:

| Key | Value |
|---|---|
| `OPENAI_API_KEY` | your key (or set `PRIMARY_LLM=groq`, `GROQ_API_KEY=…`, `EMBEDDING_PROVIDER=huggingface`) |
| `JWT_SECRET_KEY` | the random string generated above |
| `POSTGRES_PASSWORD` | `postgres` if you used the Docker command above |
| `ADMIN_SIGNUP_CODE` | any secret code — needed to create the first **admin** account |

```bash
# 5 · run
uv run uvicorn main:app --reload
```

Web UI at **http://localhost:8000** (redirects to `/ui/`) · API docs at **http://localhost:8000/docs**
First start is slower if you use `EMBEDDING_PROVIDER=huggingface` (the embedding and reranker models are downloaded once).

### First use (2 minutes)
1. Open http://localhost:8000 → **Sign up** → *Account type: Admin* → enter your `ADMIN_SIGNUP_CODE`.
2. **Documents** tab → upload a PDF → wait for **ready**.
3. **Chat** tab → ask a question about it. Then **Usage** and **Evaluation** tabs (admin only).
4. A second account (*User*) sees only the Chat tab.

### Check that everything works
```bash
uv run pytest                      # unit + integration, offline, no API keys (needs PostgreSQL from .env; DB tests skip if unreachable)
uv run pytest tests_e2e            # the whole application, step by step (real DB + ChromaDB; fake LLM/embeddings)

# with the server running and your real key in .env — checks the live pipeline over HTTP:
uv run python scripts/smoke_test.py                                   # sample PDF
uv run python scripts/smoke_test.py --admin-code "<ADMIN_SIGNUP_CODE>" --pdf my.pdf --question "…?"
uv run python scripts/smoke_test.py --admin-code "<ADMIN_SIGNUP_CODE>" --eval     # + a small DeepEval run

uv run python scripts/inspect_document.py my.pdf --mode fast          # what does the parser extract from MY file?
```
The offline tests create throw-away databases on the PostgreSQL server from `.env` (user needs the right to `CREATE DATABASE`; the Docker `postgres` user has it).
Browser (UI) tests: `cd tests/ui && npm install jsdom` — see the header of `tests/ui/e2e_server.py`.

---

## 🧭 How to Use MediRAG (end-to-end)

### 1 · One-time setup (whoever runs the server)

```bash
cp .env.example .env
```

Edit `.env`:

| Setting | What to put |
|---|---|
| `OPENAI_API_KEY` **or** `GROQ_API_KEY` | at least one LLM key |
| `EMBEDDING_PROVIDER` | `openai` (needs `OPENAI_API_KEY`) — or `huggingface` if you only have a Groq key |
| `JWT_SECRET_KEY` | long random string: `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | any strong password |
| `ADMIN_SIGNUP_CODE` | a secret code only admins know |

```bash
docker compose up --build      # API + PostgreSQL     (or: make dev, with your own PostgreSQL)
```

Open **http://localhost:8000**. The first start is slower: PostgreSQL tables are created and, with
`huggingface` embeddings / the reranker enabled, models are downloaded once.

### 2 · Admin workflow (prepare the knowledge base, then measure quality)

1. **Sign up** → *Sign up* tab → *Account type: Admin* → enter the **admin signup code** → *Create account*.
2. **Documents tab → Upload a document.** Pick the file, who may use it — *Everyone* (users **and** admins can
   retrieve it) or *Admins only* — and the **parsing mode** (see *Document parsing* below). The upload returns at once; the table
   shows **processing → ready** by itself (or **failed** with the reason). Only `ready` documents are searchable. Use **Delete** to
   remove a document and all its chunks.
3. **Chat tab** — ask a few questions to sanity-check the answers and the cited sources.
4. **Usage tab** — see who is using the chatbot (see *Reading the Usage tab* below) and disable accounts if needed.
5. **Evaluation tab** (admin only):
   1. Click **Generate test set from my documents** (the judge LLM writes a question + reference answer per sampled chunk). The bundled `golden_dataset.json` is generic and will score poorly on *your* documents.
   2. Choose the metrics (all 14 by default), optionally limit the number of cases (start with 5–10), and press **Run evaluation**. A progress bar follows the job; the page stays usable.
   3. Read the dashboard (see below). Download **MD / CSV / JSON** for reports or papers.

### 3 · Normal user workflow

1. **Sign up** as *User* (no code needed) and log in. Every request needs a valid login; an admin can disable your account at any time (you are then signed out immediately).
2. Users only **chat** — they never see Documents, Usage or Evaluation. **Chat tab** → type a medical question (or click an example). You get:
   - an answer that **appears word by word** while it is being written, built **only from the documents you have access to**, ending with a medical disclaimer. Press **■ Stop** to cancel it (the partial answer is shown but not saved to your history);
   - **source chips** — click one to see the passage that was used;
   - a refusal bubble (yellow) if the safety guardrails block the request (off-topic, harmful, crisis…).
3. **New chat** starts a fresh conversation; earlier chats stay in the sidebar of that browser.
   Sessions last `JWT_EXPIRE_MINUTES` (default 60), then you are asked to log in again.

| | User | Admin |
|---|:-:|:-:|
| Chat, sources, own chat history | ✅ | ✅ |
| Retrieves documents tagged *Everyone* | ✅ | ✅ |
| Retrieves documents tagged *Admins only* | ❌ | ✅ |
| See / upload documents (Documents tab) | ❌ | ✅ |
| Usage dashboard, enable/disable users | ❌ | ✅ |
| Evaluation tab / `/evaluation/*` API | ❌ | ✅ |

The server re-checks the account **in the database on every request**, so disabling or demoting a user takes
effect immediately (not when their token expires). Chat sessions are private to the user who started them — even admins cannot read another user's chat.

### 4 · Reading the Usage tab (admin)

Pick a range (24 h · 7 d · 30 d · 90 d · all time); the page refreshes every 30 s.

- **KPI cards** — registered users, *active users* (sent ≥ 1 request in the range), requests (with blocked / error counts),
  LLM tokens (prompt in · completion out), **estimated** LLM cost, average response time.
- **Requests per day** chart (hover a bar for tokens and users) and a **per-model** table.
- **Users table** — every registered user, even those with 0 requests: requests, blocked / errors, tokens in / out / total,
  estimated cost, average latency, last request, last login. Click a username to see only their recent requests.
  **Disable / Enable** blocks or restores an account (you cannot disable yourself).
- **Recent requests** — time, user, result (`ok` / `blocked` / `error`), model, tokens, latency.

What is (not) recorded: per request only *metadata* (user, time, status, model, tokens, latency) — **never the question or answer text**.
Tokens are the provider's reported usage (a ~4 chars/token estimate is used only if a provider reports none). Cost is an **estimate**
from a built-in price table (`app/core/pricing.py`) — verify against your provider's pricing and override with
`LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M`. Only the main answer-generation call is counted: NeMo guardrail
calls and the evaluation judge are **not** included. Usage rows are kept indefinitely (no retention job yet).

### 5 · Reading the evaluation dashboard

- **Bars** show each metric's mean score (0–1); the black tick is the pass threshold. **↓** metrics
  (Hallucination, Bias, Toxicity) are *lower-is-better*. Colour follows the **pass rate** (green ≥ 80 %, amber ≥ 50 %).
- **Retrieval low** (Contextual Precision/Recall/Relevancy) → the right passages are not being found: tune
  `CHUNK_SIZE`, `RETRIEVER_K`, `RERANKER_TOP_N`, or the embedding model.
- **Retrieval fine but Faithfulness / Hallucination bad** → the LLM is adding things not in the context: tighten the prompt or switch model.
- **Safety low** → check *Failed metrics* and the case explorer (click a row to see the question, answer, retrieved chunks and every metric's reason).
- **Guardrail accuracy** = share of cases where the guardrail blocked exactly what the dataset says it should.
- Every case makes several judge-LLM calls, so a run **costs API tokens** and takes minutes. Set `EVAL_JUDGE_MODEL` to a stronger model for more reliable judging. Only one run can be active at a time; finished reports are kept in `evaluation/reports/`.

### 6 · Using the API directly (optional)

```bash
BASE=http://localhost:8000/api/v1
curl -X POST $BASE/auth/signup -H 'Content-Type: application/json' \
     -d '{"username":"alice","password":"strong-pass-123","role":"user"}'
TOKEN=$(curl -s -X POST $BASE/auth/login -d 'username=alice&password=strong-pass-123' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST $BASE/chat/query -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"message":"What are the symptoms of diabetes?","session_id":"demo-1"}'
```

**Streaming:** `POST /chat/stream` takes the same body but answers with Server-Sent Events:

```bash
curl -N -X POST $BASE/chat/stream -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"message":"What are the symptoms of diabetes?","session_id":"demo-2"}'
```

| Event | Data | Meaning |
|---|---|---|
| `meta` | `session_id, sources, retrieval_method, model_used` | first frame, sent once retrieval is done |
| `token` | `text` | next piece of the answer (the medical disclaimer arrives as the last token) |
| `replace` | `text` | rare: the output guardrail rewrote the answer, show this text instead of what was streamed |
| `done` | `answer, sources, model_used, guardrail_passed, first_token_ms, total_ms` | final, authoritative answer — exactly what is saved to history |
| `error` | `detail` | generation failed after streaming started (partial text may have been sent) |

Authentication (401), session ownership (403) and input-guardrail refusals (422) are ordinary HTTP errors returned *before* any
streaming starts. If the client disconnects, the LLM call is cancelled, nothing is saved to chat history, and the request shows as *stopped* in the Usage tab.

Admin-only calls (`/documents/upload`, `/documents/list`, `/admin/usage/*`, `/evaluation/*`) need an admin token — see the interactive docs at `/docs`.

### 7 · Good to know

- This is an **educational tool, not medical advice**; answers are limited to your uploaded documents.
- **Streaming and the output guardrail:** the output rail needs the *complete* answer, so it runs after the last token. Normally it only appends the disclaimer (streamed last). If it adds a safety warning to the *start* of the answer, the page first shows the raw text and then swaps in the final text (`replace` event). Evaluation runs always use the complete, non-streamed answer.
- Behind a reverse proxy, disable response buffering for `/api/v1/chat/stream` (the app sends `X-Accel-Buffering: no`; for nginx also `proxy_buffering off;`), otherwise the answer arrives all at once.
- Stopped answers are logged with an *estimated* token count (the provider never reports usage for a cancelled call) and count towards the estimated cost.
- Evaluation test cases run with the dataset's `role` (default `user`), so only documents tagged *Everyone* are evaluated.
- Evaluation job *status* lives in memory (lost on restart); finished *reports* are files and persist.
- Hybrid retrieval re-scores the dense (ChromaDB) candidates with BM25 and fuses both rankings with RRF; BM25 does not search the whole corpus independently.
- The LLM provider is chosen at startup (`PRIMARY_LLM`, or Groq if there is no OpenAI key). There is no automatic switch to the other provider if a call fails — the user gets an error and it is logged as `error` in the Usage tab.
- Chat sessions that existed before the ownership feature have no owner and are no longer readable through the API.

---

## 📄 Document parsing

Upload accepts **PDF** always, and — when [Docling](https://github.com/docling-project/docling) is installed — **DOCX, PPTX, HTML,
Markdown and images (PNG / JPG / TIFF)**. Parsing runs in a background worker (one document at a time, because OCR is
memory-hungry), and the text is chunked **along sections and tables**: every chunk keeps its heading path and page number, tables
stay in their own chunk as Markdown (header row repeated if a table has to be split), and answers cite `file · p.N`.

| Mode | What it does | Use for |
|---|---|---|
| `fast` | pypdf text extraction only | text PDFs; no extra install |
| `deep` | Docling: layout analysis, table structure, OCR | scans, tables, DOCX/PPTX/images |
| `auto` *(default)* | pypdf first; switches to Docling **only if the PDF looks scanned** (little extractable text) and Docling is installed | mixed collections. Tables in a *text* PDF are not detected in `auto` — pick `deep` for those |

Choose the mode per upload (Documents tab) or set the default with `PARSER=auto|fast|deep`. Other formats always use Docling.

**Install Docling (optional, heavy — PyTorch + OCR models, everything runs locally):**

```bash
pip install -r requirements-docling.txt
python scripts/download_docling_models.py       # one-off download of the model weights (needs internet once)
# → set DOCLING_MODELS_DIR in .env, or leave it: ./docling_models is picked up automatically
docker compose build --build-arg WITH_DOCLING=true     # or WITH_DOCLING=true in .env; bakes the models into the image
```

**Look before you upload** — see exactly what each parser extracts from *your* files:

```bash
python scripts/inspect_document.py report.pdf --mode fast
python scripts/inspect_document.py report.pdf --mode deep --chunks 5
```

What parsing can and cannot do:
- ✅ text, headings, lists, tables (as Markdown), text inside scans/images (OCR), figure captions.
- ❌ It does not *understand* pictures or charts, and **medical images are deliberately not interpreted**. Handwriting, very poor scans and
  complex merged-cell tables can still come out wrong — inspect important documents.
- ⏱ Deep parsing takes seconds to minutes per document and several GB of RAM (Docling reports ~2.5 GB with the lightweight PDF
  backend, ~6 GB with the default one — measure on your server). `DOCLING_TIMEOUT_S` (default 900) caps one document.
- 🔒 Docling processes files locally; nothing is sent to a cloud service.
- Tuning knobs: `DOCLING_OCR`, `DOCLING_TABLES`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `TABLE_CHUNK_SIZE`, `MAX_UPLOAD_MB`.

> **Verification status:** DOCX / HTML / Markdown conversion was tested with the real Docling package, and page-numbered PDF handling
> with a Docling-structured document. The **PDF layout / OCR models were not run in the development environment** (the model
> downloads were unreachable). After installing Docling, run `scripts/inspect_document.py` and `scripts/smoke_test.py` on your own documents.

---

## 🐳 Docker Deployment

```bash
docker compose up --build        # API + PostgreSQL
docker compose up -d --build     # Background
docker compose logs -f api       # Follow logs
docker compose down              # Stop
```

---

## ⚙️ Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | ✅* | — | OpenAI key (*or Groq) |
| `GROQ_API_KEY` | ✅* | — | Groq key (used when `PRIMARY_LLM=groq` or there is no OpenAI key) |
| `JWT_SECRET_KEY` | ✅ | — | Secret for JWT signing |
| `POSTGRES_PASSWORD` | ✅ | — | PostgreSQL password |
| `ADMIN_SIGNUP_CODE` | ✅ (prod) | — | Code required to sign up as admin. Empty = admin signup allowed in development, **disabled** in production |
| `PARSER` | ⬜ | `auto` | Default parsing mode: `auto` / `fast` / `deep` (see *Document parsing*) |
| `DOCLING_MODELS_DIR` | ⬜ | `./docling_models` if present | Offline Docling model folder |
| `MAX_UPLOAD_MB` | ⬜ | `50` | Upload size limit |
| `PRIMARY_LLM` | ⬜ | `openai` | `openai` or `groq` |
| `EMBEDDING_PROVIDER` | ⬜ | `openai` | `openai` or `huggingface` |
| `USE_HYBRID_SEARCH` | ⬜ | `true` | Enable BM25 + semantic fusion |
| `USE_RERANKER` | ⬜ | `true` | Enable cross-encoder reranking |
| `ENABLE_GUARDRAILS` | ⬜ | `true` | Enable 4-layer guardrails |
| `RETRIEVER_K` | ⬜ | `6` | Docs to retrieve before rerank |
| `RERANKER_TOP_N` | ⬜ | `3` | Docs sent to LLM after rerank |
| `LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M` | ⬜ | built-in table | Flat USD price per 1M tokens for the dashboard's cost **estimate** |
| `EVAL_JUDGE_PROVIDER` | ⬜ | = `PRIMARY_LLM` | Judge LLM for evaluation: `openai` or `groq` |
| `EVAL_JUDGE_MODEL` | ⬜ | provider default | Use a stronger model for more reliable scores |

---

## 🛡️ Guardrails System

```
Query
  │
  ▼
Layer 1 — INPUT GUARDRAIL
  · Off-topic blocking (non-medical queries)
  · Harmful content detection
  · Crisis / self-harm redirect to emergency services
  · Min-length validation
  │
  ▼  (PASS)
Layer 2 — CONTEXT GUARDRAIL
  · Checks if retrieved chunks are sufficient
  · Returns "not found" message instead of hallucinating
  │
  ▼  (PASS)
  [LLM generates answer]
  │
  ▼
Layer 3 — OUTPUT GUARDRAIL
  · Strips dangerous specific dosage instructions
  · Strips definitive diagnostic statements
  · Appends mandatory medical disclaimer
  │
  ▼
Layer 4 — HALLUCINATION CHECK
  · Detects fabrication signal phrases
  · Logs warning if detected
  │
  ▼
Final Response (safe, cited, disclaimed)
```

---

## 📏 RAG Evaluation (DeepEval)

MediRAG ships with an evaluation suite built on [DeepEval](https://github.com/confident-ai/deepeval).
It runs a golden dataset through the **same** retriever, reranker, LLM and guardrails as production
(but without touching PostgreSQL) and scores every answer with LLM-as-a-judge metrics.

### Metrics

| Group | Metric | What it checks |
|---|---|---|
| Retrieval | `ContextualPrecision` | relevant chunks are ranked above irrelevant ones |
| Retrieval | `ContextualRecall` | retrieved context covers the expected answer |
| Retrieval | `ContextualRelevancy` | share of retrieved context that is relevant |
| Generation | `Faithfulness` | every claim is supported by the retrieved context |
| Generation | `AnswerRelevancy` | the answer addresses the question |
| Generation | `Hallucination` ↓ | answer contradicts the context |
| Generation | `MedicalCorrectness` (G-Eval) | agrees with the reference answer |
| Generation | `CitationQuality` (G-Eval) | sources are cited and grounded |
| Safety | `MedicalSafety` (G-Eval) | no diagnosis / prescription, refers to a professional |
| Safety | `NonAdvice` | no diagnosis or dosage prescription |
| Safety | `Misuse` | stays within the medical domain |
| Safety | `Bias` ↓ · `Toxicity` ↓ · `PIILeakage` | content safety |

↓ = lower is better. Retrieval/faithfulness metrics are skipped automatically for cases blocked by a
guardrail (only safety metrics are applied to refusals). A non-DeepEval **guardrail accuracy** is also
reported (`blocked == expect_block`).

### Run it

```bash
pip install -r requirements.txt        # includes deepeval + pytest
# the judge uses OPENAI_API_KEY or GROQ_API_KEY from .env
# optional: EVAL_JUDGE_PROVIDER=openai|groq  EVAL_JUDGE_MODEL=<model>

make eval                                              # all 14 metrics, sample dataset
python -m evaluation.run_eval --metrics retrieval      # a group
python -m evaluation.run_eval --metrics Faithfulness AnswerRelevancy --limit 5
python -m evaluation.run_eval --threshold 0.7 --threshold-for Hallucination=0.3
python -m evaluation.run_eval --fail-under 0.8         # exit 1 below 80 % (CI gate)
python -m evaluation.run_eval --list-metrics

pytest evaluation -v                                   # one test per golden case
deepeval test run evaluation/test_rag_deepeval.py      # same, via DeepEval CLI
```

Reports (JSON with full detail, CSV of scores, Markdown summary) are written to `evaluation/reports/`.

### Use your own documents

The bundled `evaluation/data/golden_dataset.json` contains generic medical questions. For meaningful
numbers, evaluate against the PDFs you actually ingested:

```bash
make eval-synth                                        # writes evaluation/data/synthetic.json
python -m evaluation.run_eval --dataset evaluation/data/synthetic.json
```

Golden record format: `id`, `input`, `expected_output`, `category`
(`factual | safety | off_topic | crisis | out_of_kb`), `role`, `expect_block`.

### Does the whole pipeline work? — three levels of testing

```bash
# 1 · Offline, no API keys (CI-friendly): real PostgreSQL + real ChromaDB + real parsing/retrieval/chain/streaming/usage code;
#     only the LLM and the embedding model are faked.
make test            # unit + integration            (pytest evaluation tests)
make test-e2e        # the whole application, step by step (pytest tests_e2e)

# 2 · LIVE, with your real OpenAI/Groq key (set only in the server's .env — the script never sees it):
docker compose up --build          # or: make dev
python scripts/smoke_test.py                              # sample PDF → parse → answer + page citations → streaming → guardrails → usage
python scripts/smoke_test.py --pdf my.pdf --question "What is the recommended dose of X?"
python scripts/smoke_test.py --eval                       # + a small DeepEval run (spends judge tokens)

# 3 · Quality, not just plumbing: Evaluation tab → "Generate from my documents" → Run, then compare modes (fast vs deep).
```

### Running the tests

```bash
pytest evaluation tests -v        # DB tests use a throw-away database; skipped if PostgreSQL is unreachable
# point them at your server with POSTGRES_HOST / POSTGRES_USER / POSTGRES_PASSWORD
cd tests/ui && npm install jsdom  # UI end-to-end tests (see the header of tests/ui/e2e_server.py)
```

---

## 📁 Project Structure

```
MediRAG/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py        # Router aggregator
│   │       ├── auth.py            # Signup, login (JWT)
│   │       ├── chat.py            # POST /chat/query, POST /chat/stream (SSE), GET /chat/history
│   │       ├── documents.py       # POST /documents/upload, GET /list (admin only)
│   │       ├── admin.py           # Usage analytics + enable/disable users (admin only)
│   │       ├── evaluation.py      # DeepEval runs, datasets, reports (admin only)
│   │       └── health.py          # GET /health
│   ├── core/
│   │   ├── config.py              # Pydantic settings (all from .env)
│   │   ├── pricing.py             # LLM cost estimate table
│   │   └── security.py            # JWT + per-request account check, password hashing
│   ├── db/
│   │   ├── database.py            # psycopg2 connection pool
│   │   ├── models.py              # Table creation/migrations + users, sessions, docs CRUD
│   │   └── usage.py               # usage_logs writes + dashboard aggregate queries
│   ├── guardrails/
│   │   └── nemo_guardrails.py     # NeMo Guardrails + rule fallback
│   ├── rag/
│   │   ├── embeddings/
│   │   │   ├── base.py            # Abstract embedding interface
│   │   │   ├── openai_embeddings.py
│   │   │   ├── hf_embeddings.py   # HuggingFace (free, local)
│   │   │   └── router.py          # Provider selection
│   │   ├── retriever/
│   │   │   └── hybrid_retriever.py # Semantic + BM25 + RRF fusion
│   │   ├── reranker/
│   │   │   └── cross_encoder.py   # ms-marco-MiniLM reranker
│   │   ├── chain.py               # Full RAG pipeline
│   │   ├── ingestion.py           # Upload validation + background parse → chunk → embed → ChromaDB
│   │   ├── parsing/               # pypdf fast path, Docling deep path, auto escalation
│   │   ├── chunking.py            # Section/table-aware chunks with page metadata
│   │   ├── context.py             # Tagged context + citations (shared with evaluation)
│   │   ├── llm_router.py          # OpenAI / Groq selection (config-time)
│   │   ├── llm_usage.py           # Read token usage from LLM replies
│   │   ├── streaming.py           # Token streaming core (output-rail aware)
│   │   └── vectorstore.py         # ChromaDB singleton
│   ├── schemas/
│   │   ├── auth.py                # SignupRequest, TokenResponse
│   │   ├── chat.py                # ChatRequest, ChatResponse, Source
│   │   └── docs.py                # UploadResponse, DocsListResponse
│   └── utils/
│       ├── logger.py              # Rotating file + console logger
│       └── exceptions.py          # RAGException, GuardrailException
├── app/static/                    # Web UI (index.html, app.js, style.css — no build step)
├── tests/                         # pytest: usage SQL + HTTP integration (real PostgreSQL)
│   └── ui/                        # jsdom end-to-end UI tests (stub + real backend)
├── evaluation/                    # DeepEval RAG evaluation suite
│   ├── judge.py                   # Judge LLM (OpenAI / Groq)
│   ├── metrics.py                 # 14 metrics, grouped
│   ├── pipeline.py                # RAG runner exposing retrieval context
│   ├── runner.py · report.py      # Orchestration, JSON/CSV/MD reports
│   ├── service.py                 # Background jobs + report store (used by API/UI)
│   ├── run_eval.py                # CLI
│   ├── synthesize.py              # Golden set from ingested docs
│   ├── test_rag_deepeval.py       # pytest / `deepeval test run`
│   └── data/golden_dataset.json   # Sample golden dataset
├── scripts/                       # smoke_test.py (live), inspect_document.py, download_docling_models.py
├── tests_e2e/                     # whole-application pipeline test (real main.app)
├── requirements-docling.txt       # optional deep-parsing dependencies
├── docs/
│   ├── architecture.svg/.png/.pdf # Research-style architecture figure
│   └── generate_architecture.py   # Regenerates the figure
├── docs_store/                    # Uploaded PDFs (gitignored)
├── chroma_db/                     # ChromaDB vector data (gitignored)
├── logs/                          # Application logs (gitignored)
├── main.py                        # FastAPI entry point
├── Makefile                       # Dev commands
├── Dockerfile                     # Multi-stage production image
├── docker-compose.yml             # API + PostgreSQL
├── requirements.txt
└── .env.example
```

---

