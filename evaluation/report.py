"""Report writers: JSON (full), CSV (per-case scores), Markdown (human summary)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from evaluation.metrics import GROUPS
from evaluation.runner import CaseResult


def _fmt(v, nd: int = 3) -> str:
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def case_to_dict(r: CaseResult) -> dict:
    g, o = r.golden, r.output
    return {
        "id": g.id,
        "category": g.category,
        "input": g.input,
        "expected_output": g.expected_output,
        "actual_output": o.answer,
        "retrieval_context": o.retrieval_context,
        "sources": o.sources,
        "model_used": o.model_used,
        "retrieval_method": o.retrieval_method,
        "latency_s": round(o.latency_s, 3),
        "blocked": o.blocked,
        "block_stage": o.block_stage,
        "expect_block": g.expect_block,
        "guardrail_correct": r.guardrail_correct,
        "error": r.error,
        "metrics": {
            m.name: {
                "score": m.score, "threshold": m.threshold, "success": m.success,
                "reason": m.reason, "skipped": m.skipped, "error": m.error,
            }
            for m in r.metrics
        },
    }


def write_json(results: list[CaseResult], summary: dict, path: Path, config: dict) -> Path:
    payload = {"config": config, "summary": summary, "cases": [case_to_dict(r) for r in results]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_csv(results: list[CaseResult], path: Path) -> Path:
    names = list(dict.fromkeys(m.name for r in results for m in r.metrics))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "blocked", "latency_s", *names])
        for r in results:
            scores = {m.name: m.score for m in r.metrics}
            w.writerow([r.golden.id, r.golden.category, r.output.blocked,
                        round(r.output.latency_s, 3), *[scores.get(n, "") if scores.get(n) is not None else "" for n in names]])
    return path


def write_markdown(results: list[CaseResult], summary: dict, path: Path, config: dict) -> Path:
    L: list[str] = []
    L.append("# MediRAG — DeepEval Evaluation Report\n")
    L.append(f"- **Generated:** {summary['generated_at']}")
    L.append(f"- **Judge model:** `{config.get('judge', 'n/a')}`")
    L.append(f"- **Cases:** {summary['n_cases']} (errors: {summary['n_errors']})")
    L.append(f"- **Guardrail accuracy:** {_fmt(summary['guardrail_accuracy'])}")
    L.append(f"- **Latency:** mean {_fmt(summary['latency_mean_s'])}s · p95 {_fmt(summary['latency_p95_s'])}s\n")

    L.append("## Group scores (higher = better)\n")
    L.append("| Group | Mean |\n|---|---|")
    for g in GROUPS:
        L.append(f"| {g} | {_fmt(summary['group_means'].get(g))} |")

    L.append("\n## Metric results\n")
    L.append("| Metric | Group | Mean | Pass rate | Threshold | Scored | Skipped | Errors |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, m in summary["metrics"].items():
        arrow = " ↓" if m["lower_is_better"] else ""
        L.append(f"| {name}{arrow} | {m['group']} | {_fmt(m['mean'])} | {_fmt(m['pass_rate'])} | "
                 f"{_fmt(m['threshold'], 2)} | {m['n_scored']} | {m['n_skipped']} | {m['n_errors']} |")
    L.append("\n_↓ = lower is better (threshold is the maximum allowed score)._")

    L.append("\n## Pass rate by category\n")
    L.append("| Category | Cases | Fully passed |\n|---|---|---|")
    for cat, c in summary["categories"].items():
        L.append(f"| {cat} | {c['cases']} | {c['passed']} |")

    failures = [(r, m) for r in results for m in r.metrics if m.success is False]
    L.append(f"\n## Failed metrics ({len(failures)})\n")
    if not failures:
        L.append("None 🎉")
    for r, m in failures:
        L.append(f"- **{r.golden.id}** · {m.name} = {_fmt(m.score)} (threshold {_fmt(m.threshold, 2)})"
                 f"\n  - Q: {r.golden.input}\n  - Reason: {m.reason or 'n/a'}")

    errs = [r for r in results if r.error] 
    if errs:
        L.append("\n## Pipeline errors\n")
        for r in errs:
            L.append(f"- **{r.golden.id}**: {r.error}")

    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def print_console(summary: dict) -> None:
    print("\n" + "=" * 66)
    print(f" MediRAG evaluation — {summary['n_cases']} cases, guardrail acc "
          f"{_fmt(summary['guardrail_accuracy'])}")
    print("=" * 66)
    print(f" {'Metric':<22}{'Group':<12}{'Mean':>7}{'Pass%':>8}{'n':>5}{'skip':>6}{'err':>5}")
    print("-" * 66)
    for name, m in summary["metrics"].items():
        pr = "—" if m["pass_rate"] is None else f"{m['pass_rate'] * 100:.0f}%"
        print(f" {name + (' ↓' if m['lower_is_better'] else ''):<22}{m['group']:<12}"
              f"{_fmt(m['mean']):>7}{pr:>8}{m['n_scored']:>5}{m['n_skipped']:>6}{m['n_errors']:>5}")
    print("-" * 66)
    print(" Group means: " + " | ".join(f"{g}={_fmt(summary['group_means'].get(g))}" for g in GROUPS))
    print("=" * 66)
