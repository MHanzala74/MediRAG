"""
Synthesize a golden dataset from the documents you actually ingested.

Samples chunks from ChromaDB and asks the judge LLM to write one question and a
reference answer per chunk, grounded ONLY in that chunk. The result is a ready
to use golden file for `python -m evaluation.run_eval --dataset ...`.

    python -m evaluation.synthesize --n 25 --out evaluation/data/synthetic.json
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from evaluation.dataset import Golden, save_dataset


class _QA(BaseModel):
    question: str
    answer: str


_PROMPT = """You are creating an evaluation set for a medical question-answering system.
Using ONLY the passage below, write ONE realistic question a patient or clinician
might ask, and a concise reference answer that is fully supported by the passage.
The question must be understandable without seeing the passage.

Passage (from "{source}"):
\"\"\"{chunk}\"\"\"
"""


def synthesize_from_chunks(chunks: list[dict], judge, n: int, seed: int = 42, min_chars: int = 200) -> list[Golden]:
    """chunks: [{"text": str, "source": str, "role": str}, ...]"""
    pool = [c for c in chunks if len(c["text"]) >= min_chars]
    random.Random(seed).shuffle(pool)
    goldens: list[Golden] = []
    for c in pool:
        if len(goldens) >= n:
            break
        try:
            qa = judge.generate(_PROMPT.format(source=c["source"], chunk=c["text"]), schema=_QA)
        except Exception as exc:
            print(f"  skipped a chunk ({type(exc).__name__}: {exc})")
            continue
        goldens.append(Golden(
            id=f"synth-{len(goldens) + 1:03d}",
            input=qa.question.strip(),
            expected_output=qa.answer.strip(),
            category="factual",
            role=c.get("role", "user"),
            metadata={"source": c["source"]},
        ))
    return goldens


def load_chunks_from_chroma() -> list[dict]:
    from app.rag.vectorstore import get_vectorstore

    data = get_vectorstore().get(include=["documents", "metadatas"])
    return [
        {"text": t, "source": (m or {}).get("source", "unknown"), "role": (m or {}).get("role", "user")}
        for t, m in zip(data["documents"], data["metadatas"])
    ]


def main(argv=None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Synthesize a golden dataset from ingested documents")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--out", default="evaluation/data/synthetic.json")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    from evaluation.judge import MediRAGJudge

    chunks = load_chunks_from_chroma()
    if not chunks:
        raise SystemExit("ChromaDB is empty — upload documents via /api/v1/documents/upload first.")
    goldens = synthesize_from_chunks(chunks, MediRAGJudge(), args.n, args.seed)
    print(f"Wrote {len(goldens)} cases → {save_dataset(goldens, Path(args.out))}")


if __name__ == "__main__":
    main()
