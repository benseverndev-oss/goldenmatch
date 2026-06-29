"""Tests for the A/B-independent distill utilities (capture filter + disjoint split)."""
from __future__ import annotations

import build_dataset
import capture_pairs


def test_clean_pairs_filters_empty_and_dedupes():
    recs = [
        {"text": "Acme acquired Beta.", "entities": [{"name": "Acme", "type": "org"}],
         "relationships": [{"subj": 0, "predicate": "acquired", "obj": 1}]},
        {"text": "Acme acquired Beta.", "entities": [{"name": "Acme", "type": "org"}],
         "relationships": [{"subj": 0, "predicate": "acquired", "obj": 1}]},  # dup text
        {"text": "no edges here", "entities": [{"name": "X", "type": "t"}], "relationships": []},
        {"text": "", "entities": [{"name": "X", "type": "t"}],
         "relationships": [{"subj": 0, "predicate": "p", "obj": 1}]},  # empty text
    ]
    out = list(capture_pairs.clean_pairs(recs))
    assert len(out) == 1  # dup dropped, edge-less dropped, empty-text dropped
    assert out[0]["text"] == "Acme acquired Beta."


def test_split_is_document_disjoint_and_deterministic():
    recs = [{"text": f"doc number {i}", "entities": [], "relationships": []} for i in range(200)]
    a = build_dataset.split_records(recs, val=0.1, heldout=0.1)
    b = build_dataset.split_records(recs, val=0.1, heldout=0.1)
    # deterministic
    assert [r["text"] for r in a[0]] == [r["text"] for r in b[0]]
    train, va, held = a
    texts = lambda rs: {r["text"] for r in rs}  # noqa: E731
    # disjoint partitions, full coverage
    assert texts(train) & texts(va) == set()
    assert texts(train) & texts(held) == set()
    assert texts(va) & texts(held) == set()
    assert len(train) + len(va) + len(held) == 200
    # roughly the requested proportions (deterministic hash, so allow a band)
    assert 10 <= len(held) <= 30 and 10 <= len(va) <= 30


def test_schema_report_counts():
    recs = [{"text": "t", "entities": [{"name": "A", "type": "org"}],
             "relationships": [{"subj": 0, "predicate": "acquired", "obj": 1}]}]
    rep = build_dataset.schema_report(recs)
    assert rep["predicates"] == {"acquired": 1}
    assert rep["entity_types"] == {"org": 1}
