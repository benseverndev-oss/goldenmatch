"""Load the SP6 authored QA fact layer (`dataset/qa.jsonl`).

Each QA item attaches distinct facts to DIFFERENT surface-form records of one
entity. `load_qa_facts` flattens every item's attachments into a global
`record_id -> [facts]` map for `build_kg(..., facts=...)`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_QA_PATH = Path(__file__).resolve().parent.parent / "dataset" / "qa.jsonl"


@dataclass(frozen=True)
class QAItem:
    qa_id: str
    entity_id: str
    seed_surface: str
    question: str
    facts: dict[int, str]  # record_id -> fact attached to that surface form
    gold_answer: str

    @property
    def gold_facts(self) -> list[str]:
        return [self.facts[k] for k in sorted(self.facts)]


def load_qa(path: Path | None = None) -> list[QAItem]:
    p = path or _QA_PATH
    items: list[QAItem] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        items.append(
            QAItem(
                qa_id=d["qa_id"],
                entity_id=d["entity_id"],
                seed_surface=d["seed_surface"],
                question=d["question"],
                gold_answer=d["gold_answer"],
                facts={int(k): v for k, v in d["facts"].items()},
            )
        )
    return items


def load_qa_facts(items: list[QAItem] | None = None) -> dict[int, list[str]]:
    """Global `record_id -> [facts]` over all QA items (for `build_kg`)."""
    items = items if items is not None else load_qa()
    out: dict[int, list[str]] = {}
    for it in items:
        for rid, fact in it.facts.items():
            out.setdefault(rid, []).append(fact)
    return out
