"""
Evaluation endpoints (admin only) — power the Evaluation tab of the web UI.

    GET    /evaluation/options                 metrics, groups, datasets, judge status
    POST   /evaluation/run                     start a DeepEval run (background job)
    POST   /evaluation/datasets/synthesize     generate a golden set from indexed documents
    GET    /evaluation/jobs/{job_id}           poll job progress
    GET    /evaluation/reports                 list finished runs
    GET    /evaluation/reports/{id}            full report (summary + per-case results)
    GET    /evaluation/reports/{id}/download   ?format=md|csv|json
    DELETE /evaluation/reports/{id}
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import require_admin
from evaluation import service

router = APIRouter(prefix="/evaluation", tags=["Evaluation"], dependencies=[Depends(require_admin)])


class RunRequest(BaseModel):
    dataset: str = Field("golden_dataset.json", description="File name from /evaluation/options")
    metrics: list[str] = Field(default_factory=lambda: ["all"], description="Metric names and/or groups")
    limit: int | None = Field(None, ge=1, description="Evaluate only the first N cases")
    threshold: float = Field(0.5, ge=0, le=1)
    thresholds: dict[str, float] = Field(default_factory=dict, description="Per-metric overrides")
    concurrency: int = Field(2, ge=1, le=8)
    use_guardrails: bool = True
    eval_raw: bool = False


class SynthesizeRequest(BaseModel):
    n: int = Field(15, ge=1, le=100, description="Number of question/answer pairs to generate")


def _guard_judge() -> None:
    if service.judge_factory is None and not (settings.openai_api_key or settings.groq_api_key):
        raise HTTPException(400, "No judge LLM configured — set OPENAI_API_KEY or GROQ_API_KEY in .env")


def _call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except service.BusyError as e:
        raise HTTPException(409, str(e))
    except service.NotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/options", summary="Metrics, datasets and judge status")
def get_options():
    return service.options()


@router.post("/run", status_code=202, summary="Start an evaluation run")
def run(req: RunRequest):
    _guard_judge()
    job = _call(service.start_evaluation, service.EvalParams(**req.model_dump()))
    return job.to_dict()


@router.post("/datasets/synthesize", status_code=202, summary="Generate a golden dataset from indexed documents")
def synthesize(req: SynthesizeRequest):
    _guard_judge()
    return _call(service.start_synthesis, req.n).to_dict()


@router.get("/jobs/{job_id}", summary="Job status / progress")
def job_status(job_id: str):
    return _call(service.get_job, job_id).to_dict()


@router.get("/reports", summary="List finished evaluation runs")
def list_reports():
    return {"reports": service.list_reports()}


@router.get("/reports/{report_id}", summary="Full evaluation report")
def get_report(report_id: str):
    return _call(service.get_report, report_id)


@router.get("/reports/{report_id}/download", summary="Download a report file")
def download_report(report_id: str, format: str = Query("md", pattern="^(md|csv|json)$")):
    path = _call(service.report_file, report_id, format)
    media = {"md": "text/markdown", "csv": "text/csv", "json": "application/json"}[format]
    return FileResponse(path, media_type=media, filename=path.name)


@router.delete("/reports/{report_id}", summary="Delete a report")
def delete_report(report_id: str):
    _call(service.delete_report, report_id)
    return {"deleted": report_id}
