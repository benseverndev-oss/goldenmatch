"""extraction-F1: did real extraction recover the gold entities + edge per doc.
Pure -- operates on a gold (src,dst) surface pair + a goldengraph Extraction."""
from __future__ import annotations

from dataclasses import dataclass

from erkgbench.qa_e2e.scorecard_llm import extraction_counts, f1_from_counts


@dataclass
class _M:
    name: str
    typ: str = "concept"


@dataclass
class _R:
    subj: int
    predicate: str
    obj: int


@dataclass
class _Ex:
    mentions: list
    relationships: list


def test_perfect_extraction_is_f1_one():
    ex = _Ex([_M("Acme"), _M("Rocket")], [_R(0, "made", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 2 and c["ent_fp"] == 0 and c["ent_fn"] == 0
    assert c["rel_tp"] == 1 and c["rel_fp"] == 0 and c["rel_fn"] == 0


def test_missing_entity_drops_recall_and_loses_the_edge():
    ex = _Ex([_M("Acme")], [])  # dst entity + the edge both missing
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 1 and c["ent_fn"] == 1
    assert c["rel_tp"] == 0 and c["rel_fn"] == 1


def test_spurious_entity_drops_precision():
    ex = _Ex([_M("Acme"), _M("Rocket"), _M("Noise")], [_R(0, "made", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 2 and c["ent_fp"] == 1


def test_relation_matches_either_direction_ignoring_predicate():
    # edge authored dst->src with a different predicate word still counts
    ex = _Ex([_M("Rocket"), _M("Acme")], [_R(0, "built by", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["rel_tp"] == 1 and c["rel_fp"] == 0


def test_normalization_case_insensitive():
    ex = _Ex([_M("ACME"), _M(" rocket ")], [_R(0, "made", 1)])
    c = extraction_counts("Acme", "Rocket", ex)
    assert c["ent_tp"] == 2 and c["rel_tp"] == 1


def test_f1_from_counts():
    assert f1_from_counts(2, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    r = f1_from_counts(1, 1, 1)
    assert r["precision"] == 0.5 and r["recall"] == 0.5 and r["f1"] == 0.5
    assert f1_from_counts(0, 0, 0)["f1"] == 0.0  # empty -> 0, no ZeroDivision
