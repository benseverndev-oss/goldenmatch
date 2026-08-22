"""Tests for the MGSA-JW prototype.

These pin the behaviour the benchmark conclusion rests on -- above all that the
token peak really does what the paper claims, so the negative verdict is a
verdict on a WORKING implementation rather than on a broken one.
"""

from __future__ import annotations

import pytest

from scripts.mgsa_jw.mgsa import _align_tokens, _tokenize, convj, convjw, mgsa_jw

# The paper's motivating pair (Cohen2003 ucd-people).
SWAPPED = ("Mrs. Yvonne Abbott", "Abbott, Yvonne")


def test_convjw_collapses_on_token_swap():
    """The failure MGSA-JW exists to fix, reproduced.

    The ADBIS abstract reports every ConvJW configuration under 5% F1 on
    ucd-people, treating a swapped name as indistinguishable from noise.
    """
    assert convjw(*SWAPPED) < 0.30


def test_mgsa_jw_rescues_the_token_swap():
    assert mgsa_jw(*SWAPPED) > 0.80


def test_token_peak_is_what_rescues_it():
    """Ablation: without the second peak the rescue disappears.

    This is the load-bearing test. If it ever fails, the benchmark's negative
    result would be measuring a no-op rather than the real algorithm.
    """
    with_peak = mgsa_jw(*SWAPPED, token_peak=True)
    without = mgsa_jw(*SWAPPED, token_peak=False)
    assert with_peak - without > 0.40


def test_pure_swap_scores_perfect():
    assert mgsa_jw("Yvonne Abbott", "Abbott Yvonne") == pytest.approx(1.0)


@pytest.mark.parametrize("fn", [convj, convjw, mgsa_jw])
@pytest.mark.parametrize(
    "a,b",
    [
        ("John Smith", "John Smith"),
        ("John Smith", "Jane Doe"),
        ("", ""),
        ("abc", ""),
        ("Mrs. Yvonne Abbott", "Abbott, Yvonne"),
        ("a b c d e", "e d c b a"),
    ],
)
def test_scores_stay_in_unit_interval(fn, a, b):
    assert 0.0 <= fn(a, b) <= 1.0


@pytest.mark.parametrize("fn", [convj, convjw, mgsa_jw])
def test_identity_is_one(fn):
    assert fn("Yvonne Abbott", "Yvonne Abbott") == pytest.approx(1.0)


@pytest.mark.parametrize("fn", [convj, convjw, mgsa_jw])
def test_empty_pair_conventions(fn):
    assert fn("", "") == 1.0
    assert fn("abc", "") == 0.0
    assert fn("", "abc") == 0.0


def test_published_convj_can_exceed_one():
    """The published ConvJ is not bounded above by 1.

    Algorithm 3 takes a per-character max with no one-to-one constraint, so
    several characters of S1 may claim the same character of S2: M_w is bounded
    by |S1| but not by |S2|, and M_w/|S2| in eq. (20) overflows. Pinned rather
    than fixed -- it is a reason MGSA-JW's one-to-one assignment is the better
    starting point for any port, and `clamp=True` hides it by default.
    """
    assert convj("aa", "a", clamp=False) > 1.0
    assert convj("aa", "a") == 1.0  # clamped


def test_mgsa_jw_is_bounded_by_construction():
    """One-to-one assignment caps matched mass at min(n, m), so no overflow."""
    for a, b in [("aa", "a"), ("aaaa", "a"), ("aaaaaa", "aa")]:
        assert 0.0 <= mgsa_jw(a, b) <= 1.0


def test_tokenizer_records_offsets():
    toks = _tokenize("Mrs. Yvonne Abbott")
    assert [t.text for t in toks] == ["Mrs", "Yvonne", "Abbott"]
    assert [t.start for t in toks] == [0, 5, 12]


def test_token_alignment_is_one_to_one_and_deterministic():
    t1 = _tokenize("john john smith")
    t2 = _tokenize("smith john")
    aligned = _align_tokens(t1, t2, measure="jaro_winkler", sigma=2.0, q_floor=0.0)
    targets = [ib for ib, _ in aligned.values()]
    assert len(targets) == len(set(targets)), "a target token was claimed twice"
    assert len(aligned) <= min(len(t1), len(t2))
    again = _align_tokens(t1, t2, measure="jaro_winkler", sigma=2.0, q_floor=0.0)
    assert aligned == again


def test_symmetry_is_not_assumed():
    """MGSA-JW is not symmetric, and callers must not assume it is.

    The convolution iterates over S1 and the Winkler prefix is order-independent,
    but the greedy assignment breaks ties by S1 index, so argument order can
    move the score. Documented here because a scorer registered in goldenmatch
    would need a canonical argument order (or an explicit symmetrisation).
    """
    a, b = "john a smith", "smith john"
    assert mgsa_jw(a, b) == pytest.approx(mgsa_jw(a, b))  # deterministic
    # Not asserting equality of mgsa_jw(a, b) and mgsa_jw(b, a): they may differ.
    assert 0.0 <= mgsa_jw(b, a) <= 1.0
