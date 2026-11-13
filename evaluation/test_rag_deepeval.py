"""
DeepEval / pytest integration — one test per golden case.

    pytest evaluation/test_rag_deepeval.py -v
    deepeval test run evaluation/test_rag_deepeval.py

Environment:
    EVAL_DATASET   path to golden file   (default: sample dataset)
    EVAL_METRICS   comma list of metrics/groups (default: retrieval + core generation + safety judge)
Skipped automatically when no OPENAI_API_KEY / GROQ_API_KEY is configured.
"""
import asyncio
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from deepeval import assert_test  # noqa: E402

from app.core.config import settings  # noqa: E402
from evaluation.dataset import DEFAULT_DATASET, load_dataset  # noqa: E402
from evaluation.metrics import build_named_metrics, missing_fields  # noqa: E402
from evaluation.pipeline import run_pipeline  # noqa: E402
from evaluation.runner import make_test_case  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (settings.openai_api_key or settings.groq_api_key),
    reason="No LLM API key configured — set OPENAI_API_KEY or GROQ_API_KEY",
)

SELECTION = os.getenv(
    "EVAL_METRICS", "retrieval,Faithfulness,AnswerRelevancy,MedicalSafety"
).split(",")
GOLDENS = load_dataset(os.getenv("EVAL_DATASET", DEFAULT_DATASET))


@pytest.fixture(scope="module")
def judge():
    from evaluation.judge import MediRAGJudge

    return MediRAGJudge()


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda g: g.id)
def test_medirag_case(golden, judge):
    out = asyncio.run(run_pipeline(golden.input, golden.role))

    # Guardrail behaviour is part of the contract.
    assert out.blocked == golden.expect_block, (
        f"expected blocked={golden.expect_block}, got blocked={out.blocked} "
        f"({out.block_stage or 'no guardrail'})"
    )
    if out.blocked:
        return

    tc = make_test_case(golden, out)
    metrics = [m for n, m in build_named_metrics(judge, SELECTION, async_mode=False)
               if not missing_fields(n, tc)]
    assert_test(tc, metrics)
