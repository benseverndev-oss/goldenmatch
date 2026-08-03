"""Per-decision explainer for the local ER-matcher verdict (model-free)."""
from __future__ import annotations

import pytest
from goldenmatch.core.er_matcher.explainer import (
    _CONFLICT_THRESHOLD,
    DEFAULT_FIELD_IMPORTANCE,
    PERSON_FIELD_CAUSAL_RANKING,
    PERSON_FIELD_IMPORTANCE,
    PERSON_IMPORTANCE_FAITHFULNESS_R2,
    PERSON_SIGNAL_FAITHFULNESS_R2,
    PERSON_SIGNAL_IMPORTANCE,
    PairExplanation,
    explain_pair,
    field_agreement,
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


class TestTokenAwareAgreement:
    def test_reordered_verbose_product_title_now_agrees(self):
        # jaro-winkler alone reads 0.69 here (below the 0.85 "agree" threshold, so
        # the old basis scored this real match as merely "partial"); token overlap
        # lifts it to 0.80. Real improvement, and deliberately not overstated.
        a, b = "Sony 60GB PS3", "PlayStation 3 60 GB Sony"
        import jellyfish
        jw = jellyfish.jaro_winkler_similarity(a, b)
        assert jw == pytest.approx(0.693, abs=0.01)
        assert field_agreement(a, b) == pytest.approx(0.80, abs=0.01)
        assert field_agreement(a, b) > jw

    def test_never_lowers_a_string_match(self):
        # max() with jaro-winkler: token overlap can only ADD agreement.
        import jellyfish
        for a, b in [("John", "Jon"), ("Smith", "Smyth"), ("Leeds", "Leeds"),
                     ("1990-01-01", "1990-01-02"), ("baker", "bakers")]:
            assert field_agreement(a, b) >= jellyfish.jaro_winkler_similarity(a, b)

    def test_person_fields_unchanged_by_tokenization(self):
        # single-token person values have no token overlap to gain -> jaro-winkler
        import jellyfish
        for a, b in [("John", "Jon"), ("Smith", "Smyth")]:
            assert field_agreement(a, b) == pytest.approx(
                jellyfish.jaro_winkler_similarity(a, b)
            )

    def test_alphanumeric_boundary_split(self):
        from goldenmatch.core.er_matcher.explainer import token_agreement
        # "60GB" must align with "60 GB", "PS3" with "PS 3"
        assert token_agreement("60GB", "60 GB") == pytest.approx(1.0)
        assert token_agreement("PS3", "ps 3") == pytest.approx(1.0)

    def test_single_token_side_uses_stricter_jaccard(self):
        from goldenmatch.core.er_matcher.explainer import token_agreement
        # a bare "sony" inside a long title must NOT score 1.0
        assert token_agreement("Sony", "Sony Ericsson W810i phone") < 0.5

    def test_disjoint_values_add_nothing(self):
        from goldenmatch.core.er_matcher.explainer import token_agreement
        # unrelated products share no tokens, so the token term contributes 0 and
        # the result is exactly jaro-winkler's own floor (~0.42) -- the new metric
        # must not manufacture agreement where there is none.
        a, b = "Canon EOS 5D", "Whirlpool dishwasher"
        import jellyfish
        assert token_agreement(a, b) == 0.0
        assert field_agreement(a, b) == pytest.approx(
            jellyfish.jaro_winkler_similarity(a, b)
        )
        assert field_agreement(a, b) < _CONFLICT_THRESHOLD  # still reads as conflict

    def test_missing_and_exact_semantics_preserved(self):
        assert field_agreement(None, "x") is None
        assert field_agreement("", "x") is None
        assert field_agreement("x", "x") == 1.0


class TestHighFaithfulnessMode:
    def test_off_by_default(self):
        exp = explain_pair(_a(), _a(), COLS, match=True, confidence=0.9)
        assert exp.signal_contributions is None
        assert exp.faithfulness_r2 == PERSON_IMPORTANCE_FAITHFULNESS_R2

    def test_reports_the_richer_number_and_contributions(self):
        exp = explain_pair(
            _a(), _a(), COLS, match=True, confidence=0.9, high_faithfulness=True
        )
        assert exp.faithfulness_r2 == PERSON_SIGNAL_FAITHFULNESS_R2
        assert exp.faithfulness_r2 > PERSON_IMPORTANCE_FAITHFULNESS_R2
        assert exp.signal_contributions
        assert f"~{int(PERSON_SIGNAL_FAITHFULNESS_R2 * 100)}%" in exp.rationale

    def test_prose_is_unchanged_by_the_mode(self):
        # the richer basis informs the NUMBER, never the human story
        b = _a() | {"first_name": "Zebedee"}
        low = explain_pair(_a(), b, COLS, match=False, confidence=0.7)
        high = explain_pair(
            _a(), b, COLS, match=False, confidence=0.7, high_faithfulness=True
        )
        assert [s.field for s in low.signals] == [s.field for s in high.signals]
        assert [s.importance for s in low.signals] == [s.importance for s in high.signals]

    def test_contributions_are_signed_and_ranked(self):
        exp = explain_pair(
            _a(), _a(), COLS, match=True, confidence=0.9, high_faithfulness=True
        )
        cs = exp.signal_contributions
        mags = [abs(c["contribution"]) for c in cs]
        assert mags == sorted(mags, reverse=True)
        assert all(
            c["contribution"] == pytest.approx(c["weight"] * c["value"]) for c in cs
        )

    def test_rollup_must_not_be_used_for_display(self):
        # Documents WHY the mode never touches the prose: summed per field these
        # weights rank occupation first and first_name last, inverting the ablation
        # ranking. If this ever stops holding, revisit the two-mode split.
        roll: dict[str, float] = {}
        for k, v in PERSON_SIGNAL_IMPORTANCE.items():
            roll[k.split("__")[0]] = roll.get(k.split("__")[0], 0.0) + abs(v)
        order = sorted(roll, key=lambda f: -roll[f])
        assert order[0] == "occupation"
        assert order[-1] == "first_name"
        assert order[0] == PERSON_FIELD_CAUSAL_RANKING[-1]

    def test_no_op_on_unknown_schema(self):
        exp = explain_pair(
            {"sku": "A"}, {"sku": "B"}, ["sku"], match=False, confidence=0.4,
            high_faithfulness=True,
        )
        assert exp.signal_contributions is None
        assert exp.faithfulness_r2 is None


def test_counterfactuals_absent_by_default():
    exp = explain_pair(_a(), _a(), COLS, match=True, confidence=0.9)
    assert exp.counterfactuals is None
    assert "Counterfactual" not in exp.rationale


def test_counterfactual_flip_is_surfaced():
    # base P(match)=0.9; removing birth_place drops it to 0.2 -> verdict reverses
    exp = explain_pair(
        _a(), _a(), COLS, match=True, confidence=0.9,
        p_without={"birth_place": 0.2, "first_name": 0.88, "surname": 0.91},
    )
    cfs = {c.field: c for c in exp.counterfactuals}
    assert cfs["birth_place"].flips_verdict is True
    assert cfs["first_name"].flips_verdict is False
    assert exp.counterfactuals[0].field == "birth_place"  # ranked by impact
    assert "REVERSE" in exp.rationale
    assert "birth_place" in exp.rationale


def test_counterfactual_no_flip_says_so():
    exp = explain_pair(
        _a(), _a(), COLS, match=True, confidence=0.9,
        p_without={"birth_place": 0.85, "first_name": 0.80},
    )
    assert all(not c.flips_verdict for c in exp.counterfactuals)
    assert "no single field decides this" in exp.rationale


def test_counterfactual_baseline_uses_p_match_not_confidence():
    # NO-MATCH at confidence 0.9 means P(match)=0.1. Removing a field that raises
    # P(match) to 0.7 must count as a flip; comparing against 0.9 would miss it
    # AND report the delta with the wrong sign.
    exp = explain_pair(
        _a(), _a(), COLS, match=False, confidence=0.9, p_without={"dob": 0.7},
    )
    cf = exp.counterfactuals[0]
    assert cf.flips_verdict is True
    assert cf.delta < 0  # the field was pushing AWAY from match
    assert cf.delta == pytest.approx(0.1 - 0.7)


def test_counterfactual_delta_sign_and_ordering():
    exp = explain_pair(
        _a(), _a(), COLS, match=True, confidence=0.8,
        p_without={"a_small": 0.75, "b_big": 0.10, "c_negative": 0.95},
    )
    deltas = {c.field: c.delta for c in exp.counterfactuals}
    assert deltas["b_big"] == pytest.approx(0.7)
    assert deltas["c_negative"] == pytest.approx(-0.15)
    assert [c.field for c in exp.counterfactuals] == ["b_big", "c_negative", "a_small"]


def test_counterfactuals_survive_to_dict():
    exp = explain_pair(
        _a(), _a(), COLS, match=True, confidence=0.9, p_without={"dob": 0.1},
    )
    d = exp.to_dict()
    assert d["counterfactuals"][0]["field"] == "dob"
    assert d["counterfactuals"][0]["flips_verdict"] is True


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
