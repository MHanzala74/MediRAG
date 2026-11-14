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
| 🤖 Multi-model LLM | OpenAI gpt-4o-mini (primary) → Groq llama-3.3-70b (auto-fallback) |
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
| `POST` | `/api/v1/documents/upload` | Admin | Upload PDF |
| `GET` | `/api/v1/documents/list` | User | List accessible docs |
| `POST` | `/api/v1/chat/query` | User | Ask a medical question |
| `GET` | `/api/v1/chat/history/{session_id}` | User | Get chat history |
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
- Python 3.11+
- PostgreSQL (or use Docker)
- API keys: [OpenAI](https://platform.openai.com/api-keys) or [Groq](https://console.groq.com)

```bash
git clone https://github.com/MHanzala74/MediRAG.git
cd MediRAG

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # Fill in your API keys

make dev               # Start with auto-reload
```

Web UI at **http://localhost:8000** (redirects to `/ui/`) · API docs at **http://localhost:8000/docs**

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
2. **Documents tab → Upload a document.** Pick a PDF and who may use it:
   *Everyone* (users **and** admins can retrieve it) or *Admins only*. It is parsed, chunked and indexed.
3. **Chat tab** — ask a few questions to sanity-check the answers and the cited sources.
4. **Evaluation tab** (admin only):
   1. Click **Generate test set from my documents** (the judge LLM writes a question + reference answer per sampled chunk). The bundled `golden_dataset.json` is generic and will score poorly on *your* documents.
   2. Choose the metrics (all 14 by default), optionally limit the number of cases (start with 5–10), and press **Run evaluation**. A progress bar follows the job; the page stays usable.
   3. Read the dashboard (see below). Download **MD / CSV / JSON** for reports or papers.

### 3 · Normal user workflow

1. **Sign up** as *User* (no code needed) and log in.
2. **Chat tab** → type a medical question (or click an example). You get:
   - an answer built **only from the documents you have access to**, ending with a medical disclaimer;
   - **source chips** — click one to see the passage that was used;
   - a refusal bubble (yellow) if the safety guardrails block the request (off-topic, harmful, crisis…).
3. **New chat** starts a fresh conversation; earlier chats stay in the sidebar of that browser.
   Sessions last `JWT_EXPIRE_MINUTES` (default 60), then you are asked to log in again.

| | User | Admin |
|---|:-:|:-:|
| Chat, sources, history | ✅ | ✅ |
| Retrieves documents tagged *Everyone* | ✅ | ✅ |
| Retrieves documents tagged *Admins only* | ❌ | ✅ |
| Upload documents | ❌ | ✅ |
| Evaluation tab / `/evaluation/*` API | ❌ | ✅ |

### 4 · Reading the evaluation dashboard

- **Bars** show each metric's mean score (0–1); the black tick is the pass threshold. **↓** metrics
  (Hallucination, Bias, Toxicity) are *lower-is-better*. Colour follows the **pass rate** (green ≥ 80 %, amber ≥ 50 %).
- **Retrieval low** (Contextual Precision/Recall/Relevancy) → the right passages are not being found: tune
  `CHUNK_SIZE`, `RETRIEVER_K`, `RERANKER_TOP_N`, or the embedding model.
- **Retrieval fine but Faithfulness / Hallucination bad** → the LLM is adding things not in the context: tighten the prompt or switch model.
- **Safety low** → check *Failed metrics* and the case explorer (click a row to see the question, answer, retrieved chunks and every metric's reason).
- **Guardrail accuracy** = share of cases where the guardrail blocked exactly what the dataset says it should.
- Every case makes several judge-LLM calls, so a run **costs API tokens** and takes minutes. Set `EVAL_JUDGE_MODEL` to a stronger model for more reliable judging. Only one run can be active at a time; finished reports are kept in `evaluation/reports/`.

### 5 · Using the API directly (optional)

```bash
BASE=http://localhost:8000/api/v1
curl -X POST $BASE/auth/signup -H 'Content-Type: application/json' \
     -d '{"username":"alice","password":"strong-pass-123","role":"user"}'
TOKEN=$(curl -s -X POST $BASE/auth/login -d 'username=alice&password=strong-pass-123' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST $BASE/chat/query -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"message":"What are the symptoms of diabetes?","session_id":"demo-1"}'
```

Admin-only calls (`/documents/upload`, `/evaluation/run`, …) need an admin token — see the interactive docs at `/docs`.

### 6 · Good to know

- This is an **educational tool, not medical advice**; answers are limited to your uploaded documents.
- Evaluation test cases run with the dataset's `role` (default `user`), so only documents tagged *Everyone* are evaluated.
- Evaluation job *status* lives in memory (lost on restart); finished *reports* are files and persist.
- Hybrid retrieval re-scores the dense (ChromaDB) candidates with BM25 and fuses both rankings with RRF; BM25 does not search the whole corpus independently.
- Known limitation: `GET /chat/history/{session_id}` does not check that the session belongs to the caller — session ids are random UUIDs, but treat them as secrets.

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
| `GROQ_API_KEY` | ✅* | — | Groq key (fallback LLM) |
| `JWT_SECRET_KEY` | ✅ | — | Secret for JWT signing |
| `POSTGRES_PASSWORD` | ✅ | — | PostgreSQL password |
| `ADMIN_SIGNUP_CODE` | ✅ (prod) | — | Code required to sign up as admin. Empty = admin signup allowed in development, **disabled** in production |
| `PRIMARY_LLM` | ⬜ | `openai` | `openai` or `groq` |
| `EMBEDDING_PROVIDER` | ⬜ | `openai` | `openai` or `huggingface` |
| `USE_HYBRID_SEARCH` | ⬜ | `true` | Enable BM25 + semantic fusion |
| `USE_RERANKER` | ⬜ | `true` | Enable cross-encoder reranking |
| `ENABLE_GUARDRAILS` | ⬜ | `true` | Enable 4-layer guardrails |
| `RETRIEVER_K` | ⬜ | `6` | Docs to retrieve before rerank |
| `RERANKER_TOP_N` | ⬜ | `3` | Docs sent to LLM after rerank |
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

---

## 📁 Project Structure

```
RAG-Medical-Assistant/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py        # Router aggregator
│   │       ├── auth.py            # Signup, login (JWT)
│   │       ├── chat.py            # POST /chat/query, GET /chat/history
│   │       ├── documents.py       # POST /documents/upload, GET /list
│   │       └── health.py          # GET /health
│   ├── core/
│   │   ├── config.py              # Pydantic settings (all from .env)
│   │   └── security.py            # JWT create/decode, password hashing
│   ├── db/
│   │   ├── database.py            # psycopg2 connection pool
│   │   └── models.py              # Table creation + CRUD
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
│   │   ├── ingestion.py           # PDF → chunks → ChromaDB
│   │   ├── llm_router.py          # OpenAI / Groq selection
│   │   └── vectorstore.py         # ChromaDB singleton
│   ├── schemas/
│   │   ├── auth.py                # SignupRequest, TokenResponse
│   │   ├── chat.py                # ChatRequest, ChatResponse, Source
│   │   └── docs.py                # UploadResponse, DocsListResponse
│   └── utils/
│       ├── logger.py              # Rotating file + console logger
│       └── exceptions.py          # RAGException, GuardrailException
├── app/static/                    # Web UI (index.html, app.js, style.css — no build step)
├── tests/ui/                      # jsdom end-to-end UI test + stub server
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

