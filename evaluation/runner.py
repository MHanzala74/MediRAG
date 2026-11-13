"""
Evaluation runner: golden dataset → RAG pipeline → DeepEval metrics → results.
"""
from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from deepeval.test_case import LLMTestCase

from evaluation.dataset import Golden
from evaluation.metrics import GROUPS, METRICS, build_named_metrics, missing_fields
from evaluation.pipeline import PipelineResult, run_pipeline

PipelineFn = Callable[[str, str, bool | None], Awaitable[PipelineResult]]
MetricsFactory = Callable[[], list[tuple[str, object]]]


@dataclass
class MetricResult:
    name: str
    group: str
    score: float | None = None
    threshold: float | None = None
    success: bool | None = None
    reason: str | None = None
    skipped: str | None = None      # why it was not run
    error: str | None = None
    lower_is_better: bool = False


@dataclass
class CaseResult:
    golden: Golden
    output: PipelineResult
    metrics: list[MetricResult] = field(default_factory=list)
    error: str | None = None

    @property
    def guardrail_correct(self) -> bool:
        return self.output.blocked == self.golden.expect_block


def make_test_case(golden: Golden, out: PipelineResult, eval_raw: bool = False) -> LLMTestCase:
    answer = (out.raw_answer or out.answer) if eval_raw and out.raw_answer else out.answer
    return LLMTestCase(
        input=golden.input,
        actual_output=answer,
        expected_output=golden.expected_output,
        retrieval_context=out.retrieval_context or None,
        context=out.retrieval_context or None,      # HallucinationMetric reads `context`
        name=golden.id,
        additional_metadata={"category": golden.category, "sources": out.sources},
    )


async def _score_case(
    golden: Golden,
    out: PipelineResult,
    metrics: list[tuple[str, object]],
    eval_raw: bool,
) -> CaseResult:
    case = CaseResult(golden=golden, output=out)
    tc = make_test_case(golden, out, eval_raw)

    async def run_one(name: str, metric) -> MetricResult:
        spec = METRICS.get(name)
        group = spec.group if spec else "custom"
        res = MetricResult(
            name=name, group=group, threshold=getattr(metric, "threshold", None),
            lower_is_better=bool(spec and spec.lower_is_better),
        )
        # Guardrail-blocked answers are refusals: only safety metrics make sense.
        if out.blocked and group != "safety":
            res.skipped = f"answer blocked by {out.block_stage} guardrail"
            return res
        missing = missing_fields(name, tc)
        if missing:
            res.skipped = f"missing {', '.join(missing)}"
            return res
        try:
            await metric.a_measure(tc, _show_indicator=False)
            res.score = metric.score
            res.success = bool(metric.success)
            res.reason = getattr(metric, "reason", None)
        except Exception as exc:  # judge/API failure must not abort the run
            res.error = f"{type(exc).__name__}: {exc}"
        return res

    case.metrics = list(await asyncio.gather(*(run_one(n, m) for n, m in metrics)))
    return case


async def evaluate_dataset(
    goldens: list[Golden],
    judge,
    metric_selection: list[str] | None = None,
    threshold: float = 0.5,
    thresholds: dict[str, float] | None = None,
    concurrency: int = 2,
    use_guardrails: bool | None = None,
    eval_raw: bool = False,
    pipeline_fn: PipelineFn = run_pipeline,
    metrics_factory: MetricsFactory | None = None,
    on_progress: Callable[[int, int, CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Run every golden through the RAG pipeline and score it with DeepEval."""
    sem = asyncio.Semaphore(max(1, concurrency))
    done = 0
    results: list[CaseResult | None] = [None] * len(goldens)

    def fresh_metrics() -> list[tuple[str, object]]:
        if metrics_factory:
            return metrics_factory()
        return build_named_metrics(judge, metric_selection, threshold, thresholds)

    async def work(i: int, g: Golden) -> None:
        nonlocal done
        async with sem:
            try:
                out = await pipeline_fn(g.input, g.role, use_guardrails)
                case = await _score_case(g, out, fresh_metrics(), eval_raw)
            except Exception as exc:
                case = CaseResult(golden=g, output=PipelineResult(answer=""), error=f"{type(exc).__name__}: {exc}")
            results[i] = case
            done += 1
            if on_progress:
                on_progress(done, len(goldens), case)

    await asyncio.gather(*(work(i, g) for i, g in enumerate(goldens)))
    return [r for r in results if r is not None]


# ── aggregation ──────────────────────────────────────────────────────────
def summarize(results: list[CaseResult]) -> dict:
    """Aggregate per-metric, per-group, per-category and system-level numbers."""
    per_metric: dict[str, list[MetricResult]] = {}
    for r in results:
        for m in r.metrics:
            per_metric.setdefault(m.name, []).append(m)

    metric_rows = {}
    for name, rows in per_metric.items():
        scored = [m for m in rows if m.score is not None]
        metric_rows[name] = {
            "group": rows[0].group,
            "lower_is_better": rows[0].lower_is_better,
            "threshold": rows[0].threshold,
            "n_scored": len(scored),
            "n_skipped": sum(1 for m in rows if m.skipped),
            "n_errors": sum(1 for m in rows if m.error),
            "mean": round(statistics.fmean(m.score for m in scored), 4) if scored else None,
            "pass_rate": round(sum(1 for m in scored if m.success) / len(scored), 4) if scored else None,
        }

    group_rows = {}
    for g in GROUPS:
        # lower-is-better metrics (Bias, Toxicity, Hallucination) are inverted
        # so that every group score reads "higher = better".
        vals = [(1 - v["mean"]) if v["lower_is_better"] else v["mean"]
                for v in metric_rows.values()
                if v["group"] == g and v["mean"] is not None]
        group_rows[g] = round(statistics.fmean(vals), 4) if vals else None

    category_rows: dict[str, dict] = {}
    for r in results:
        scored = [m for m in r.metrics if m.score is not None]
        c = category_rows.setdefault(r.golden.category, {"cases": 0, "passed": 0})
        c["cases"] += 1
        if scored and all(m.success for m in scored):
            c["passed"] += 1

    latencies = [r.output.latency_s for r in results if r.output.latency_s]
    gcorrect = [r.guardrail_correct for r in results if not r.error]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_cases": len(results),
        "n_errors": sum(1 for r in results if r.error),
        "metrics": metric_rows,
        "group_means": group_rows,
        "categories": category_rows,
        "guardrail_accuracy": round(sum(gcorrect) / len(gcorrect), 4) if gcorrect else None,
        "latency_mean_s": round(statistics.fmean(latencies), 3) if latencies else None,
        "latency_p95_s": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else None,
    }
