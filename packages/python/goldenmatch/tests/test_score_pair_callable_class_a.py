"""Fast-path callable parity for Class A scorers.

PR #555 added 'ensemble' to ``_resolve_score_pair_callable``; this module
extends that to the per-pair-friendly scorers that previously returned
None ('dice', 'jaccard') or were never added ('soundex_match'). Workloads
where auto-config or explicit config picks any of these on a field were
silently falling to ``find_fuzzy_matches`` even when every other gate
was satisfied.

These tests assert that:
1. ``_resolve_score_pair_callable`` returns a callable for each name.
2. The callable is bit-equivalent (within rapidfuzz tolerance) to the
   matrix path that ``find_fuzzy_matches`` would use.
"""
from __future__ import annotations

import math

import pytest
from goldenmatch.backends.score_buckets import _resolve_score_pair_callable


@pytest.mark.parametrize("scorer_name", ["soundex_match", "dice", "jaccard"])
def test_class_a_scorer_resolves_to_callable(scorer_name):
    fn = _resolve_score_pair_callable(scorer_name)
    assert fn is not None, f"{scorer_name!r} must return a callable (was None)"
    assert callable(fn)


def test_soundex_match_matches_matrix_path():
    """Per-pair soundex_match must produce the same 0/1 score as the matrix
    path in core/scorer.py:88. Same jellyfish.soundex under the hood."""
    import jellyfish
    fn = _resolve_score_pair_callable("soundex_match")
    pairs = [
        ("Smith", "Smyth"),    # same soundex -> 1.0
        ("Robert", "Rupert"),  # same soundex -> 1.0
        ("Smith", "Jones"),    # different -> 0.0
        ("", ""),              # edge
    ]
    for a, b in pairs:
        expected = 1.0 if jellyfish.soundex(a) == jellyfish.soundex(b) else 0.0
        assert fn(a, b) == expected, f"soundex_match({a!r},{b!r})"


def test_dice_matches_matrix_path():
    """Per-pair dice must match the existing _dice_score_single in
    core/scorer.py. Note: dice + jaccard in goldenmatch are PPRL scorers --
    they operate on HEX-encoded bloom filters, not raw strings. The matrix
    path (_dice_score_matrix) does the same bit-vector math vectorized."""
    from goldenmatch.core.scorer import _dice_score_single
    fn = _resolve_score_pair_callable("dice")
    pairs = [
        ("ff", "ff"),       # identical 8-bit -> 1.0
        ("ff00", "00ff"),   # disjoint nonzero bits -> 0.0
        ("ffff", "ff00"),   # half overlap
        ("a5a5", "a5a5"),   # identical -> 1.0
    ]
    for a, b in pairs:
        assert math.isclose(fn(a, b), _dice_score_single(a, b))


def test_jaccard_matches_matrix_path():
    """Same PPRL bloom-filter constraint as dice."""
    from goldenmatch.core.scorer import _jaccard_score_single
    fn = _resolve_score_pair_callable("jaccard")
    pairs = [
        ("ff", "ff"),
        ("ff00", "00ff"),
        ("ffff", "ff00"),
        ("a5a5", "a5a5"),
    ]
    for a, b in pairs:
        assert math.isclose(fn(a, b), _jaccard_score_single(a, b))
