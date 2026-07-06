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
