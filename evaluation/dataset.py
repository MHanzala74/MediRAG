"""
Golden dataset loader.

A golden dataset is a JSON list (or JSONL file) of records:

    {
      "id": "dm-001",                    # unique id
      "category": "factual",             # factual | safety | off_topic | crisis | out_of_kb
      "input": "What are the symptoms of type 2 diabetes?",
      "expected_output": "…reference answer…",   # optional but needed for
                                                  # ContextualRecall / MedicalCorrectness
      "role": "user",                    # role used for document filtering
      "expect_block": false              # true if guardrails SHOULD refuse
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_DATASET = Path(__file__).parent / "data" / "golden_dataset.json"


@dataclass
class Golden:
    id: str
    input: str
    expected_output: str | None = None
    category: str = "factual"
    role: str = "user"
    expect_block: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def load_dataset(path: str | Path | None = None, limit: int | None = None) -> list[Golden]:
    path = Path(path) if path else DEFAULT_DATASET
    if not path.exists():
        raise FileNotFoundError(f"Golden dataset not found: {path}")

    text = path.read_text(encoding="utf-8").strip()
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = json.loads(text)

    known = set(Golden.__dataclass_fields__)
    goldens: list[Golden] = []
    for i, row in enumerate(rows):
        if "input" not in row:
            raise ValueError(f"Record #{i} in {path} is missing 'input'")
        row = dict(row)
        row.setdefault("id", f"case-{i + 1:03d}")
        extra = {k: v for k, v in row.items() if k not in known}
        clean = {k: v for k, v in row.items() if k in known}
        clean.setdefault("metadata", {}).update(extra)
        goldens.append(Golden(**clean))

    return goldens[:limit] if limit else goldens


def save_dataset(goldens: list[Golden], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([g.to_dict() for g in goldens], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
