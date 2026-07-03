from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.metrics import (  # noqa: E402
    answer_match,
    classify_answer_type,
    decay_curve,
    exact_match,
    is_entity_answer,
    judge_prompt,
    parse_judge,
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


# --- answer-type classification (entity-answerable subset) --------------------
#
# Cases drawn verbatim from the 2026-06-23 N=50 MuSiQue localize trace, which is
# what the entity-subset denominator is meant to model.


def test_classify_entity_answers():
    for g in [
        "Exeter College",
        "the Politburo",
        "Sega Genesis",
        "Firefox",
        "Lana Wood",
        "U.S. Marshal Rooster Cogburn",
    ]:
        assert classify_answer_type(g) == "entity", g
        assert is_entity_answer(g) is True


def test_classify_number_answers():
    for g in ["$72,641", "5am", "72,641", "3.5 million"]:
        assert classify_answer_type(g) == "number", g
        assert is_entity_answer(g) is False


def test_classify_date_answers():
    for g in ["11 February 1929", "February 11, 1929", "1929", "02/11/1929"]:
        assert classify_answer_type(g) == "date", g
        assert is_entity_answer(g) is False


def test_classify_phrase_answers():
    for g in [
        "built on 16-bit architectures and offered improved graphics and sound",
        "because it was cheaper to produce",
    ]:
        assert classify_answer_type(g) == "phrase", g
        assert is_entity_answer(g) is False


def test_classify_handles_empty():
    assert classify_answer_type("") == "phrase"
    assert is_entity_answer("") is False


# --- LLM-judge answer equivalence (format-fair cross-engine metric) ----------


def test_parse_judge_yes_no():
    assert parse_judge("YES") == 1.0
    assert parse_judge("yes") == 1.0
    assert parse_judge("Yes, that is correct.") == 1.0
    assert parse_judge("NO") == 0.0
    assert parse_judge("no.") == 0.0
    assert parse_judge("No, the answer differs") == 0.0


def test_parse_judge_fallback_and_empty():
    # leading hedge but a standalone yes later -> correct
    assert parse_judge("Verdict: YES") == 1.0
    # no clear verdict / empty / unrelated -> NO (0.0), never crashes
    assert parse_judge("") == 0.0
    assert parse_judge("maybe") == 0.0
    assert parse_judge(None) == 0.0  # type: ignore[arg-type]


def test_judge_prompt_includes_all_three_fields():
    p = judge_prompt("When founded?", "1976", "It was founded in 1976.")
    assert "When founded?" in p
    assert "1976" in p
    assert "It was founded in 1976." in p
    assert "YES or NO" in p
