"""
Tests for the evaluation REST API + background job service.

No API keys needed: the judge, pipeline and metrics are replaced by stubs
through the hooks in `evaluation.service`.
"""
import shutil
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.evaluation import router
from app.core.security import get_current_user, require_admin
from evaluation import service
from evaluation.pipeline import PipelineResult


class StubMetric:
    def __init__(self, score, ok=True):
        self.score, self.success, self.reason, self.threshold = score, ok, "stub", 0.5

    async def a_measure(self, tc, _show_indicator=False):
        return self.score


class StubJudge:
    def get_model_name(self):
        return "stub-judge"

    def generate(self, prompt, schema=None):
        from evaluation.synthesize import _QA
        return _QA(question="What does the passage describe?", answer="It describes a condition.")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, reports = tmp_path / "data", tmp_path / "reports"
    data.mkdir()
    reports.mkdir()
    shutil.copy(service.BASE_DIR / "data" / "golden_dataset.json", data / "golden_dataset.json")
    monkeypatch.setattr(service, "DATA_DIR", data)
    monkeypatch.setattr(service, "REPORT_DIR", reports)
    monkeypatch.setattr(service, "_jobs", {})
    gate = threading.Event()
    gate.set()

    async def pipe(q, role, g):
        gate.wait(5)
        if "Python" in q or "end my life" in q:
            return PipelineResult(answer="Refusal", blocked=True, block_stage="input", latency_s=.1)
        return PipelineResult(answer="Answer", retrieval_context=["ctx"], sources=["a.pdf"], latency_s=.2)

    monkeypatch.setattr(service, "judge_factory", lambda: StubJudge())
    monkeypatch.setattr(service, "pipeline_fn_factory", lambda: pipe)
    monkeypatch.setattr(service, "metrics_factory",
                        lambda: [("Faithfulness", StubMetric(0.9)), ("Bias", StubMetric(0.0)),
                                 ("MedicalCorrectness", StubMetric(0.3, ok=False))])
    return gate


def make_client(role="admin"):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"username": "tester", "role": role}
    return TestClient(app)


def wait_done(client, job_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/v1/evaluation/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_requires_admin(env):
    c = make_client(role="user")
    assert c.get("/api/v1/evaluation/options").status_code == 403
    assert c.post("/api/v1/evaluation/run", json={}).status_code == 403


def test_options(env):
    body = make_client().get("/api/v1/evaluation/options").json()
    assert len(body["metrics"]) == 14
    assert set(body["groups"]) == {"retrieval", "generation", "safety"}
    assert body["datasets"][0]["name"] == "golden_dataset.json"


def test_full_run_report_download_delete(env):
    c = make_client()
    r = c.post("/api/v1/evaluation/run", json={"limit": 6})
    assert r.status_code == 202
    job = wait_done(c, r.json()["id"])
    assert job["status"] == "done" and job["done"] == job["total"] == 6

    rid = job["report_id"]
    listing = c.get("/api/v1/evaluation/reports").json()["reports"]
    assert [x["id"] for x in listing] == [rid]

    rep = c.get(f"/api/v1/evaluation/reports/{rid}").json()
    assert len(rep["cases"]) == 6
    assert set(rep["summary"]["metrics"]) == {"Faithfulness", "Bias", "MedicalCorrectness"}
    assert rep["summary"]["overall_pass_rate"] is not None
    assert rep["config"]["judge"] == "stub-judge"

    md = c.get(f"/api/v1/evaluation/reports/{rid}/download?format=md")
    assert md.status_code == 200 and "DeepEval Evaluation Report" in md.text
    assert c.get(f"/api/v1/evaluation/reports/{rid}/download?format=csv").status_code == 200

    assert c.delete(f"/api/v1/evaluation/reports/{rid}").status_code == 200
    assert c.get(f"/api/v1/evaluation/reports/{rid}").status_code == 404


def test_validation_and_path_safety(env):
    c = make_client()
    assert c.post("/api/v1/evaluation/run", json={"dataset": "../../etc/passwd"}).status_code == 422
    assert c.post("/api/v1/evaluation/run", json={"dataset": "missing.json"}).status_code == 404
    assert c.post("/api/v1/evaluation/run", json={"metrics": ["Nope"]}).status_code == 422
    assert c.post("/api/v1/evaluation/run", json={"thresholds": {"Nope": 0.5}}).status_code == 422
    assert c.get("/api/v1/evaluation/reports/..%2f..%2fsecret").status_code in (404, 422)
    assert c.get("/api/v1/evaluation/reports/not_a_report").status_code == 422
    assert c.get("/api/v1/evaluation/jobs/unknown").status_code == 404


def test_only_one_job_at_a_time(env):
    gate = env
    gate.clear()                                   # keep first job running
    c = make_client()
    first = c.post("/api/v1/evaluation/run", json={"limit": 2})
    assert first.status_code == 202
    assert c.post("/api/v1/evaluation/run", json={"limit": 2}).status_code == 409
    assert c.get("/api/v1/evaluation/options").json()["active_job"] == first.json()["id"]
    gate.set()
    assert wait_done(c, first.json()["id"])["status"] == "done"


def test_synthesize_creates_dataset(env, monkeypatch):
    import evaluation.synthesize as syn

    monkeypatch.setattr(syn, "load_chunks_from_chroma",
                        lambda: [{"text": "x" * 300, "source": "a.pdf", "role": "user"}] * 5)
    c = make_client()
    r = c.post("/api/v1/evaluation/datasets/synthesize", json={"n": 3})
    assert r.status_code == 202
    job = wait_done(c, r.json()["id"])
    assert job["status"] == "done" and job["dataset"].startswith("synthetic_")
    names = [d["name"] for d in c.get("/api/v1/evaluation/options").json()["datasets"]]
    assert job["dataset"] in names


def test_synthesize_without_documents_reports_error(env, monkeypatch):
    import evaluation.synthesize as syn

    monkeypatch.setattr(syn, "load_chunks_from_chroma", lambda: [])
    c = make_client()
    job = wait_done(c, c.post("/api/v1/evaluation/datasets/synthesize", json={"n": 2}).json()["id"])
    assert job["status"] == "error" and "upload PDFs" in job["error"]
