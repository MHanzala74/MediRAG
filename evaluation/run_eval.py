"""
CLI entry point.

    python -m evaluation.run_eval                       # all metrics, sample dataset
    python -m evaluation.run_eval --metrics retrieval   # one group
    python -m evaluation.run_eval --metrics Faithfulness AnswerRelevancy --limit 5
    python -m evaluation.run_eval --dataset evaluation/data/synth.json --threshold 0.7
    python -m evaluation.run_eval --list-metrics
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from evaluation.dataset import DEFAULT_DATASET, load_dataset
from evaluation.metrics import GROUPS, METRICS


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="evaluation.run_eval", description="Evaluate MediRAG with DeepEval")
    p.add_argument("--dataset", default=str(DEFAULT_DATASET), help="golden dataset (.json / .jsonl)")
    p.add_argument("--metrics", nargs="+", default=["all"],
                   help=f"metric names and/or groups {list(GROUPS)} or 'all'")
    p.add_argument("--limit", type=int, default=None, help="evaluate only the first N cases")
    p.add_argument("--threshold", type=float, default=0.5, help="default pass threshold")
    p.add_argument("--threshold-for", action="append", default=[], metavar="METRIC=VALUE",
                   help="per-metric threshold override (repeatable)")
    p.add_argument("--concurrency", type=int, default=2, help="cases evaluated in parallel")
    p.add_argument("--no-guardrails", action="store_true", help="bypass NeMo guardrails")
    p.add_argument("--eval-raw", action="store_true",
                   help="score the raw LLM answer (before output rail + disclaimer)")
    p.add_argument("--output-dir", default=str(Path(__file__).parent / "reports"))
    p.add_argument("--fail-under", type=float, default=None,
                   help="exit with code 1 if overall pass rate is below this (0-1), for CI")
    p.add_argument("--list-metrics", action="store_true")
    return p.parse_args(argv)


def _list_metrics() -> None:
    for g in GROUPS:
        print(f"\n[{g}]")
        for n, s in METRICS.items():
            if s.group == g:
                print(f"  {n:<22}{'(lower=better) ' if s.lower_is_better else '':<15}{s.description}")


async def _main(args: argparse.Namespace) -> int:
    from evaluation.judge import MediRAGJudge
    from evaluation.report import print_console, write_csv, write_json, write_markdown
    from evaluation.runner import evaluate_dataset, summarize

    thresholds = {}
    for item in args.threshold_for:
        name, _, val = item.partition("=")
        if name not in METRICS or not val:
            raise SystemExit(f"--threshold-for expects METRIC=VALUE with a known metric, got '{item}'")
        thresholds[name] = float(val)

    goldens = load_dataset(args.dataset, args.limit)
    judge = MediRAGJudge()
    print(f"Judge: {judge.get_model_name()} | cases: {len(goldens)} | metrics: {args.metrics}")

    def progress(done, total, case):
        status = "ERROR" if case.error else ("blocked" if case.output.blocked else "ok")
        print(f"  [{done}/{total}] {case.golden.id:<14} {status}")

    results = await evaluate_dataset(
        goldens, judge,
        metric_selection=args.metrics,
        threshold=args.threshold,
        thresholds=thresholds,
        concurrency=args.concurrency,
        use_guardrails=False if args.no_guardrails else None,
        eval_raw=args.eval_raw,
        on_progress=progress,
    )
    summary = summarize(results)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    config = {"judge": judge.get_model_name(), "dataset": args.dataset, "metrics": args.metrics,
              "threshold": args.threshold, "thresholds": thresholds,
              "guardrails": not args.no_guardrails, "eval_raw": args.eval_raw}
    jp = write_json(results, summary, out_dir / f"eval_{stamp}.json", config)
    write_csv(results, out_dir / f"eval_{stamp}.csv")
    mp = write_markdown(results, summary, out_dir / f"eval_{stamp}.md", config)

    print_console(summary)
    print(f"Reports: {jp}\n         {mp}")

    if args.fail_under is not None:
        rates = [m["pass_rate"] for m in summary["metrics"].values() if m["pass_rate"] is not None]
        overall = sum(rates) / len(rates) if rates else 0.0
        print(f"Overall mean pass rate: {overall:.3f} (required ≥ {args.fail_under})")
        return 0 if overall >= args.fail_under else 1
    return 0


def main(argv=None) -> None:
    load_dotenv()
    args = _parse_args(argv)
    if args.list_metrics:
        _list_metrics()
        return
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
