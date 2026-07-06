"""Wave 2 scorer dispatch / pure-path tests (box-safe; INFERMAP_NATIVE=0)."""

from infermap.scorers.exact import _exact_score_pure
from infermap.scorers.fuzzy_name import _fuzzy_name_score_pure
from infermap.scorers.initialism import _score_pair
from infermap.types import FieldInfo


def test_exact_pure():
    assert _exact_score_pure("City", " city ") == 1.0
    assert _exact_score_pure("a", "b") == 0.0


def test_fuzzy_pure():
    assert _fuzzy_name_score_pure("city", "city") == 1.0
    assert _fuzzy_name_score_pure("abc", "xyz") == 0.0


def test_initialism_pure_abstain_and_score():
    assert _score_pair("city", "town") is None
    assert _score_pair("city", "city") is None
    s = _score_pair("assay_id", "ASSI")
    assert abs(s - (0.6 + 0.35 * (4 / 7))) < 1e-12


def test_scorer_classes_still_work():
    from infermap.scorers.exact import ExactScorer
    from infermap.scorers.fuzzy_name import FuzzyNameScorer

    a, b = FieldInfo(name="city"), FieldInfo(name="city")
    assert ExactScorer().score(a, b).score == 1.0
    assert FuzzyNameScorer().score(a, b).score == 1.0


# --- Wave 3: profile scorer ---
from infermap.scorers.profile import ProfileScorer, _profile_score_pure  # noqa: E402


def test_profile_pure_identical_profiles_is_one():
    # same dtype, equal null/uniq, equal lens, equal cards -> all 5 terms = 1.0
    s = _profile_score_pure("string", "string", 0.1, 0.1, 0.5, 0.5,
                            100.0, 100.0, 8.0, 8.0)
    assert s == 1.0


def test_profile_pure_dtype_mismatch_drops_point_four():
    # identical except dtype -> 1.0 - 0.4 = 0.6
    s = _profile_score_pure("string", "int", 0.1, 0.1, 0.5, 0.5,
                            100.0, 100.0, 8.0, 8.0)
    assert s == 0.6


def test_profile_scorer_abstains_on_zero_rows():
    src = FieldInfo(name="a", value_count=0)
    tgt = FieldInfo(name="b", value_count=10)
    assert ProfileScorer().score(src, tgt) is None


def test_profile_scorer_reasoning_unchanged():
    src = FieldInfo(name="a", dtype="string", null_rate=0.1, unique_rate=0.5,
                    value_count=100, sample_values=["abcd", "efgh"])
    tgt = FieldInfo(name="b", dtype="string", null_rate=0.1, unique_rate=0.5,
                    value_count=100, sample_values=["abcd", "efgh"])
    r = ProfileScorer().score(src, tgt)
    assert r is not None
    for part in ("dtype=match", "null_sim=", "uniq_sim=", "len_sim=", "card_sim="):
        assert part in r.reasoning
    assert r.reasoning.startswith("Profile comparison: ")


def test_profile_score_registered_in_loader():
    from infermap._native_loader import _COMPONENT_SYMBOLS, _GATED_ON
    assert _COMPONENT_SYMBOLS.get("profile_score") == "profile_score"
    assert "profile_score" in _GATED_ON
# --- Wave 4: pattern_type scorer ---
from infermap.scorers.pattern_type import (  # noqa: E402
    PatternTypeScorer,
    _classify_with_pct,
    _match_types_pure,
)


def test_match_types_pure_bitmask():
    # bit0=email, bit7=currency; "hello" matches nothing.
    assert _match_types_pure("user@example.com") == 1 << 0
    # date_iso (bit2) AND phone (bit5) co-match by construction: an 8-digit
    # 2-hyphen string satisfies phone's ^[\+\d]?(\d[\s\-\.]?){7,14}\d$ (the
    # hyphens are absorbed as optional separators). This is expected, not a bug.
    assert _match_types_pure("2026-07-06") == (1 << 2) | (1 << 5)
    assert _match_types_pure("$5") == 1 << 7
    assert _match_types_pure("hello world") == 0


def test_classify_with_pct_unchanged_behavior():
    emails = FieldInfo(name="e", sample_values=["a@b.co", "x@y.com", "p@q.net"],
                       value_count=3)
    assert _classify_with_pct(emails) == ("email", 1.0)
    # below threshold (1 of 3 is an email) -> (None, 0.0)
    mixed = FieldInfo(name="m", sample_values=["a@b.co", "hello", "world"],
                      value_count=3)
    assert _classify_with_pct(mixed) == (None, 0.0)
    # no samples -> (None, 0.0)
    empty = FieldInfo(name="z", sample_values=["  ", None], value_count=0)
    assert _classify_with_pct(empty) == (None, 0.0)


def test_pattern_type_scorer_abstain_mismatch_match():
    emails_a = FieldInfo(name="a", sample_values=["a@b.co", "x@y.com"], value_count=2)
    emails_b = FieldInfo(name="b", sample_values=["p@q.net", "m@n.org"], value_count=2)
    dates = FieldInfo(name="d", sample_values=["2026-07-06", "2025-01-02"], value_count=2)
    none_field = FieldInfo(name="n", sample_values=["  ", None], value_count=0)
    # abstain when a side has no samples
    assert PatternTypeScorer().score(emails_a, none_field) is None
    # same type -> min of pcts, reasoning names the type
    r = PatternTypeScorer().score(emails_a, emails_b)
    assert r is not None and r.score == 1.0 and "email" in r.reasoning
    # different types -> 0.0 mismatch
    r2 = PatternTypeScorer().score(emails_a, dates)
    assert r2 is not None and r2.score == 0.0 and "mismatch" in r2.reasoning
