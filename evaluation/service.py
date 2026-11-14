"""
Background jobs + report store behind the REST API and the web UI.

An evaluation (or golden-set synthesis) can take minutes because every case makes
several judge-LLM calls, so it runs in a worker thread with its own event loop and
the API only starts it and polls its progress. Only one job runs at a time.

Reports are plain files in ``evaluation/reports`` (JSON + CSV + Markdown), so they
survive restarts and can be mounted as a Docker volume.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from evaluation.dataset import load_dataset, save_dataset
from evaluation.metrics import GROUPS, METRICS, resolve_names

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"

REPORT_ID_RE = re.compile(r"^eval_\d{8}_\d{6}(_\d+)?$")
DATASET_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.(json|jsonl)$")

# Test / extension hooks (None → production behaviour).
judge_factory: Callable[[], object] | None = None
pipeline_fn_factory: Callable[[], Callable] | None = None
metrics_factory: Callable[[], list] | None = None     # -> [(name, metric), ...]


class BusyError(RuntimeError):
    """Another job is already running."""


class NotFoundError(LookupError):
    pass


# ── job model ────────────────────────────────────────────────────────────
@dataclass
class Job:
    id: str
    kind: str                       # evaluate | synthesize
    status: str = "queued"          # queued | running | done | error
    done: int = 0
    total: int = 0
    message: str = ""
    report_id: str | None = None
    dataset: str | None = None      # dataset used (evaluate) or produced (synthesize)
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "done": self.done, "total": self.total, "message": self.message,
            "report_id": self.report_id, "dataset": self.dataset, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "config": self.config,
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _active_job() -> Job | None:
    return next((j for j in _jobs.values() if j.status in ("queued", "running")), None)


def active_job_id() -> str | None:
    j = _active_job()
    return j.id if j else None


def get_job(job_id: str) -> Job:
    try:
        return _jobs[job_id]
    except KeyError:
        raise NotFoundError(f"Unknown job '{job_id}'") from None


def _register(kind: str, config: dict, dataset: str | None = None) -> Job:
    with _lock:
        if _active_job():
            raise BusyError("An evaluation job is already running")
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, config=config, dataset=dataset)
        _jobs[job.id] = job
        for old in sorted(_jobs.values(), key=lambda j: j.started_at)[:-20]:   # keep last 20
            if old.status in ("done", "error"):
                _jobs.pop(old.id, None)
        return job


# ── datasets ─────────────────────────────────────────────────────────────
def dataset_path(name: str) -> Path:
    if not DATASET_RE.match(name or ""):
        raise ValueError("Invalid dataset name")
    path = DATA_DIR / name
    if not path.is_file():
        raise NotFoundError(f"Dataset '{name}' not found")
    return path


def list_datasets() -> list[dict]:
    out = []
    for p in sorted(DATA_DIR.glob("*")):
        if p.suffix not in (".json", ".jsonl") or not DATASET_RE.match(p.name):
            continue
        try:
            goldens = load_dataset(p)
        except Exception:
            continue
        out.append({
            "name": p.name,
            "n_cases": len(goldens),
            "with_reference": sum(1 for g in goldens if g.expected_output),
            "categories": sorted({g.category for g in goldens}),
        })
    return out


# ── evaluation job ───────────────────────────────────────────────────────
@dataclass
class EvalParams:
    dataset: str = "golden_dataset.json"
    metrics: list[str] = field(default_factory=lambda: ["all"])
    limit: int | None = None
    threshold: float = 0.5
    thresholds: dict[str, float] = field(default_factory=dict)
    concurrency: int = 2
    use_guardrails: bool = True
    eval_raw: bool = False

    def validate(self) -> None:
        dataset_path(self.dataset)
        resolve_names(self.metrics)                     # raises ValueError on unknown names
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        for k, v in self.thresholds.items():
            if k not in METRICS or not 0 <= v <= 1:
                raise ValueError(f"Invalid threshold override '{k}={v}'")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be ≥ 1")
        if not 1 <= self.concurrency <= 8:
            raise ValueError("concurrency must be between 1 and 8")


def _default_judge():
    from evaluation.judge import MediRAGJudge
    return MediRAGJudge()


def _default_pipeline_fn():
    from app.guardrails.nemo_guardrails import NeMoGuardrailsManager
    from evaluation.pipeline import run_pipeline

    guards = NeMoGuardrailsManager()        # private instance → bound to this job's event loop

    def fn(query, role, use_guardrails):
        return run_pipeline(query, role, use_guardrails, guardrails=guards)

    return fn


def start_evaluation(params: EvalParams) -> Job:
    params.validate()
    cfg = {k: v for k, v in params.__dict__.items()}
    job = _register("evaluate", cfg, params.dataset)
    threading.Thread(target=_run_evaluation, args=(job, params), daemon=True, name=f"eval-{job.id}").start()
    return job


def _run_evaluation(job: Job, params: EvalParams) -> None:
    from evaluation.report import write_csv, write_json, write_markdown
    from evaluation.runner import evaluate_dataset, summarize

    try:
        job.status = "running"
        job.message = "Loading dataset"
        goldens = load_dataset(dataset_path(params.dataset), params.limit)
        job.total = len(goldens)

        job.message = "Initialising judge model"
        judge = (judge_factory or _default_judge)()
        pipeline_fn = (pipeline_fn_factory or _default_pipeline_fn)()

        def progress(done, total, case):
            job.done = done
            job.message = f"Evaluated {case.golden.id}"

        job.message = "Running RAG pipeline and DeepEval metrics"
        results = asyncio.run(evaluate_dataset(
            goldens, judge,
            metric_selection=params.metrics,
            threshold=params.threshold,
            thresholds=params.thresholds,
            concurrency=params.concurrency,
            use_guardrails=None if params.use_guardrails else False,
            eval_raw=params.eval_raw,
            pipeline_fn=pipeline_fn,
            metrics_factory=metrics_factory,
            on_progress=progress,
        ))
        summary = summarize(results)

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stem = _new_report_stem()
        config = {
            "judge": judge.get_model_name(), "dataset": params.dataset, "metrics": params.metrics,
            "threshold": params.threshold, "thresholds": params.thresholds,
            "guardrails": params.use_guardrails, "eval_raw": params.eval_raw,
        }
        write_json(results, summary, REPORT_DIR / f"{stem}.json", config)
        write_csv(results, REPORT_DIR / f"{stem}.csv")
        write_markdown(results, summary, REPORT_DIR / f"{stem}.md", config)

        job.report_id = stem
        job.message = "Finished"
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.message = "Failed"
    finally:
        job.finished_at = time.time()


def _new_report_stem() -> str:
    base = "eval_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, i = base, 1
    while (REPORT_DIR / f"{stem}.json").exists():
        i += 1
        stem = f"{base}_{i}"
    return stem


# ── synthesis job ────────────────────────────────────────────────────────
def start_synthesis(n: int) -> Job:
    if not 1 <= n <= 100:
        raise ValueError("n must be between 1 and 100")
    job = _register("synthesize", {"n": n})
    job.total = n
    threading.Thread(target=_run_synthesis, args=(job, n), daemon=True, name=f"synth-{job.id}").start()
    return job


def _run_synthesis(job: Job, n: int) -> None:
    from evaluation.synthesize import load_chunks_from_chroma, synthesize_from_chunks

    try:
        job.status = "running"
        job.message = "Reading ingested chunks from ChromaDB"
        chunks = load_chunks_from_chroma()
        if not chunks:
            raise RuntimeError("No documents are indexed yet — upload PDFs on the Documents tab first.")
        judge = (judge_factory or _default_judge)()

        def progress(done, total):
            job.done = done
            job.message = f"Generated {done}/{total} questions"

        goldens = synthesize_from_chunks(chunks, judge, n, on_progress=progress)
        if not goldens:
            raise RuntimeError("Could not generate any question from the indexed chunks "
                               "(chunks may be too short).")
        name = "synthetic_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
        save_dataset(goldens, DATA_DIR / name)
        job.dataset = name
        job.done = len(goldens)
        job.message = "Finished"
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.message = "Failed"
    finally:
        job.finished_at = time.time()


# ── reports ──────────────────────────────────────────────────────────────
_list_cache: dict[tuple[str, float], dict] = {}


def _check_report_id(report_id: str) -> None:
    if not REPORT_ID_RE.match(report_id or ""):
        raise ValueError("Invalid report id")


def list_reports() -> list[dict]:
    rows = []
    for p in sorted(REPORT_DIR.glob("eval_*.json"), reverse=True):
        if not REPORT_ID_RE.match(p.stem):
            continue
        key = (str(p), p.stat().st_mtime)
        if key not in _list_cache:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            s, c = data.get("summary", {}), data.get("config", {})
            _list_cache[key] = {
                "id": p.stem,
                "generated_at": s.get("generated_at"),
                "n_cases": s.get("n_cases"),
                "n_errors": s.get("n_errors"),
                "judge": c.get("judge"),
                "dataset": c.get("dataset"),
                "group_means": s.get("group_means", {}),
                "overall_pass_rate": s.get("overall_pass_rate"),
                "guardrail_accuracy": s.get("guardrail_accuracy"),
            }
        rows.append(_list_cache[key])
    return rows


def get_report(report_id: str) -> dict:
    _check_report_id(report_id)
    p = REPORT_DIR / f"{report_id}.json"
    if not p.is_file():
        raise NotFoundError(f"Report '{report_id}' not found")
    return json.loads(p.read_text(encoding="utf-8"))


def report_file(report_id: str, fmt: str) -> Path:
    _check_report_id(report_id)
    if fmt not in ("json", "csv", "md"):
        raise ValueError("format must be json, csv or md")
    p = REPORT_DIR / f"{report_id}.{fmt}"
    if not p.is_file():
        raise NotFoundError(f"Report '{report_id}' ({fmt}) not found")
    return p


def delete_report(report_id: str) -> None:
    _check_report_id(report_id)
    found = False
    for fmt in ("json", "csv", "md"):
        p = REPORT_DIR / f"{report_id}.{fmt}"
        if p.is_file():
            p.unlink()
            found = True
    if not found:
        raise NotFoundError(f"Report '{report_id}' not found")


# ── options for the UI ───────────────────────────────────────────────────
def options() -> dict:
    from app.core.config import settings

    return {
        "groups": list(GROUPS),
        "metrics": [
            {"name": n, "group": s.group, "description": s.description, "lower_is_better": s.lower_is_better}
            for n, s in METRICS.items()
        ],
        "datasets": list_datasets(),
        "judge_configured": judge_factory is not None or bool(settings.openai_api_key or settings.groq_api_key),
        "guardrails_enabled": settings.enable_guardrails,
        "active_job": active_job_id(),
    }
