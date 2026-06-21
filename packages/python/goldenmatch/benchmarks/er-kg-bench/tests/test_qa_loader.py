"""The authored QA fact layer must be valid against the real corpus."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.qa_loader import load_qa, load_qa_facts  # noqa: E402


def _records() -> list[dict]:
    p = _BENCH_ROOT / "dataset" / "records.csv"
    return list(csv.DictReader(p.open(encoding="utf-8")))


def test_qa_corpus_valid_against_records():
    recs = _records()
    by_id = {int(r["record_id"]): r for r in recs}
    ent_of = {int(r["record_id"]): r["entity_id"] for r in recs}
    mentions_by_entity: dict[str, set[str]] = {}
    for r in recs:
        mentions_by_entity.setdefault(r["entity_id"], set()).add(r["mention"])

    items = load_qa()
    assert len(items) >= 8
    seen: set[str] = set()
    for it in items:
        assert it.qa_id and it.qa_id not in seen
        seen.add(it.qa_id)
        assert it.entity_id in mentions_by_entity, it.entity_id
        assert it.question and it.gold_answer
        assert it.gold_facts and all(f for f in it.gold_facts)
        # seed_surface is a real mention of this entity
        assert it.seed_surface in mentions_by_entity[it.entity_id], (it.qa_id, it.seed_surface)
        # at least two DISTINCT surface forms carry facts (else no fragmentation)
        fact_forms = {by_id[rid]["mention"] for rid in it.facts}
        assert len(fact_forms) >= 2, (it.qa_id, fact_forms)
        # every fact record belongs to this entity
        for rid in it.facts:
            assert ent_of[rid] == it.entity_id, (it.qa_id, rid)


def test_qa_facts_flatten():
    facts = load_qa_facts()
    assert facts
    assert all(isinstance(v, list) and v for v in facts.values())
