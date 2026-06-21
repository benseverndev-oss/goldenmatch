from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.metrics import (  # noqa: E402
    answer_match,
    decay_curve,
    exact_match,
    supporting_fact_recall,
    token_f1,
)


def test_exact_match_normalizes_articles_punct_case():
    assert exact_match("The Acme Corp.", "acme corp") == 1.0
    assert exact_match("Ada Lovelace", "Charles Babbage") == 0.0


def test_answer_match_containment_on_free_text():
    # gold appears as a token run inside a generative sentence -> 1.0, where
    # exact_match (whole-string) reads 0.0 on the same pair.
    assert answer_match("The final entity is Acme Corp.", "Acme Corp") == 1.0
    assert exact_match("The final entity is Acme Corp.", "Acme Corp") == 0.0
    # normalization (case / articles / punctuation) still applies
    assert answer_match("...the answer: the ACME corp!", "Acme Corp") == 1.0
    # wrong answer -> 0.0
    assert answer_match("The final entity is Globex.", "Acme Corp") == 0.0
    # token-level, not raw substring: 'acme' must not match inside 'acmecorp'
    assert answer_match("acmecorp wins", "acme") == 0.0
    # empty gold is vacuously matched only by an empty prediction
    assert answer_match("anything", "") == 0.0
    assert answer_match("", "") == 1.0


def test_token_f1_partial_overlap():
    assert token_f1("Ada Lovelace", "Ada Lovelace") == 1.0
    # one of two gold tokens recovered -> P=1/1, R=1/2 -> F1=2/3
    assert abs(token_f1("Ada", "Ada Lovelace") - (2 / 3)) < 1e-9
    assert token_f1("", "Ada") == 0.0


def test_supporting_fact_recall():
    assert supporting_fact_recall(("d1", "d2", "x"), ("d1", "d2")) == 1.0
    assert supporting_fact_recall(("d1",), ("d1", "d2")) == 0.5
    assert supporting_fact_recall((), ()) == 1.0


def test_decay_curve_groups_by_hop():
    rows = [(1, 1.0), (1, 0.0), (2, 1.0), (2, 1.0), (3, 0.0)]
    assert decay_curve(rows) == {1: 0.5, 2: 1.0, 3: 0.0}
