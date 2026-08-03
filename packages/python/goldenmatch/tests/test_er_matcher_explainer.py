"""Per-decision explainer for the local ER-matcher verdict (model-free)."""
from __future__ import annotations

from goldenmatch.core.er_matcher.explainer import (
    DEFAULT_FIELD_IMPORTANCE,
    PERSON_FIELD_CAUSAL_RANKING,
    PERSON_FIELD_IMPORTANCE,
    PERSON_IMPORTANCE_FAITHFULNESS_R2,
    PairExplanation,
    explain_pair,
)

COLS = ["first_name", "surname", "dob", "birth_place", "occupation"]


def _a():
    return {"first_name": "John", "surname": "Smith", "dob": "1990-01-01",
            "birth_place": "Leeds", "occupation": "baker"}


def test_returns_pair_explanation_with_verdict():
    exp = explain_pair(_a(), _a(), COLS, match=True, confidence=0.91)
    assert isinstance(exp, PairExplanation)
    assert exp.match is True
    assert exp.confidence == 0.91
    assert len(exp.signals) == len(COLS)


def test_identical_records_all_support():
    exp = explain_pair(_a(), _a(), COLS, match=True, confidence=0.95)
    kinds = {s.field: s.kind for s in exp.signals}
    assert all(k == "support" for k in kinds.values())
    assert "MATCH" in exp.rationale
    assert exp.field_story_agrees_with_verdict is True


def test_conflict_on_discriminating_field_is_opposing():
    b = _a() | {"first_name": "Michael", "birth_place": "Bristol"}
    exp = explain_pair(_a(), b, COLS, match=False, confidence=0.80)
    kinds = {s.field: s.kind for s in exp.signals}
    assert kinds["first_name"] == "conflict"
    assert kinds["surname"] == "support"  # surname still agrees
    assert "Opposing:" in exp.rationale


def test_missing_field_classified_missing():
    b = _a() | {"birth_place": ""}
    exp = explain_pair(_a(), b, COLS, match=True, confidence=0.7)
    bp = next(s for s in exp.signals if s.field == "birth_place")
    assert bp.kind == "missing"
    assert bp.agreement is None


def test_learned_person_weights_applied_and_ranked():
    # first_name conflict (importance .42) should outrank a surname conflict (.04)
    b = _a() | {"first_name": "Zebedee", "surname": "Smythe"}
    exp = explain_pair(_a(), b, COLS, match=False, confidence=0.6)
    assert exp.learned_weights_applied is True
    assert exp.faithfulness_r2 == PERSON_IMPORTANCE_FAITHFULNESS_R2
    conflicts = [s for s in exp.signals if s.kind == "conflict"]
    conflicts_ranked = sorted(conflicts, key=lambda s: -s.importance)
    assert conflicts_ranked[0].field == "first_name"  # highest learned importance
    # first_name importance strictly greater than surname's
    imp = {s.field: s.importance for s in exp.signals}
    assert imp["first_name"] > imp["surname"]


def test_surname_and_dob_downweighted_vs_first_name():
    # surname/dob agreement tracks the verdict weakly -- these are SCORING weights
    # ("does this field's agreement track the verdict?"), not a necessity claim.
    assert PERSON_FIELD_IMPORTANCE["first_name"] > PERSON_FIELD_IMPORTANCE["surname"]
    assert PERSON_FIELD_IMPORTANCE["first_name"] > PERSON_FIELD_IMPORTANCE["dob"]
    assert PERSON_FIELD_IMPORTANCE["birth_place"] > PERSON_FIELD_IMPORTANCE["dob"]


def test_causal_ranking_is_separate_and_does_not_call_dob_ignored():
    # Ablation says the model NEEDS dob (top-3) even though its scoring weight is
    # lowest. These are different questions and the low weight must never be
    # restated as "the model ignores dob". Locking the distinction in.
    assert PERSON_FIELD_CAUSAL_RANKING[0] == "birth_place"
    assert PERSON_FIELD_CAUSAL_RANKING.index("dob") <= 2
    assert PERSON_FIELD_CAUSAL_RANKING[-1] == "occupation"
    # the two rankings genuinely disagree -- if they ever converge, re-check both
    by_weight = sorted(
        PERSON_FIELD_CAUSAL_RANKING, key=lambda f: -PERSON_FIELD_IMPORTANCE[f]
    )
    assert tuple(by_weight) != PERSON_FIELD_CAUSAL_RANKING


def test_faithfulness_is_the_measured_verdict_number():
    # 5-seed mean of the frozen weights vs the model's real P(match), cluster-disjoint
    # split, hard negatives. Guards against drifting back to a leaked/projection figure.
    assert PERSON_IMPORTANCE_FAITHFULNESS_R2 == 0.27


def test_explicit_weights_override():
    w = {"occupation": 5.0}
    b = _a() | {"occupation": "welder"}
    exp = explain_pair(_a(), b, COLS, match=False, confidence=0.5, weights=w)
    occ = next(s for s in exp.signals if s.field == "occupation")
    assert occ.importance == 5.0


def test_unknown_schema_uses_neutral_default_no_faithfulness():
    a = {"sku": "ABC-123", "price": "9.99"}
    b = {"sku": "ABC-999", "price": "9.99"}
    exp = explain_pair(a, b, ["sku", "price"], match=False, confidence=0.4)
    assert exp.learned_weights_applied is False
    assert exp.faithfulness_r2 is None
    assert all(s.importance == DEFAULT_FIELD_IMPORTANCE for s in exp.signals)


def test_field_story_disagreeing_with_verdict_is_flagged():
    # all fields agree, but the model said NO MATCH -> low-confidence explanation
    exp = explain_pair(_a(), _a(), COLS, match=False, confidence=0.51)
    assert exp.field_story_agrees_with_verdict is False
    assert "low-confidence" in exp.rationale


def test_to_dict_roundtrips():
    exp = explain_pair(_a(), _a(), COLS, match=True, confidence=0.9)
    d = exp.to_dict()
    assert d["match"] is True
    assert isinstance(d["signals"], list)
    assert d["signals"][0]["field"] == "first_name"
