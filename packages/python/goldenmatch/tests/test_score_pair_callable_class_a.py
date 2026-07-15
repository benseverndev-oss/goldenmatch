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


# ── #1781: tf_freqs threading through the bucket fast path ──────────────────


def _members_1781(res) -> frozenset:
    cl = res.clusters or {}
    return frozenset(
        frozenset(int(m) for m in c.get("members", []))
        for c in cl.values()
        if len(c.get("members", [])) > 1
    )


def test_bucket_path_applies_tf_freqs_table_1781():
    """#1781 signature regression: dedupe through the BUCKET path with
    MatchkeyField.tf_freqs populated vs stripped must produce DIFFERENT
    output. Pre-fix the resolver dropped the table, so ON == OFF
    byte-identical (the bug's signature).

    Miniaturized #1319 common-name fixture (~40 rows): 20 distinct people
    all sharing the common surname 'smith' (unique emails), one genuine
    'zorvath' duplicate pair, 18 unique fillers. With the table applied,
    identical-'smith' pairs score ~0.68 (< 0.8 threshold, no cluster) while
    the rare zorvath pair scores 1.0 (clusters either way)."""
    import goldenmatch.refdata  # noqa: F401  (registers name_freq_weighted_jw)
    import polars as pl
    from goldenmatch import dedupe_df
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    last = ["smith"] * 20 + ["zorvath"] * 2 + [f"filler{i}" for i in range(18)]
    email = (
        [f"s{i}@x.com" for i in range(20)]
        + ["z@x.com", "z@x.com"]
        + [f"f{i}@x.com" for i in range(18)]
    )
    df = pl.DataFrame({"last": last, "email": email})
    # Real relative frequencies over the 40 rows: smith 0.5, zorvath 0.05,
    # fillers 0.025 each. log_ref = log(40); rarity(smith) ~= 0.19 ->
    # weight ~= 0.675 -> identical-pair score ~= 0.675 < 0.8.
    tf_freqs = {"smith": 0.5, "zorvath": 0.05}
    tf_freqs.update({f"filler{i}": 0.025 for i in range(18)})

    def _cfg(table):
        return GoldenMatchConfig(
            backend="bucket",
            matchkeys=[MatchkeyConfig(
                name="k", type="weighted", threshold=0.8,
                fields=[MatchkeyField(
                    field="last", scorer="name_freq_weighted_jw",
                    weight=1.0, tf_freqs=table,
                )],
            )],
            blocking=BlockingConfig(
                strategy="static",
                keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase"])],
            ),
        )

    on = _members_1781(dedupe_df(df, config=_cfg(tf_freqs)))
    off = _members_1781(dedupe_df(df, config=_cfg(None)))
    # The rare-name duplicate pair clusters in BOTH runs (weight 1.0 with the
    # table; plain jw=1.0 without) -- so the diff below is table-driven, not
    # empty-vs-empty.
    assert any(len(c) == 2 for c in on), f"zorvath pair missing from ON: {on}"
    assert any(len(c) == 20 for c in off), f"20-smith cluster missing from OFF: {off}"
    assert on != off, (
        "bucket path ignored tf_freqs: ON == OFF byte-identical "
        f"(the #1781 signature). clusters={on}"
    )


def test_resolver_tf_freqs_typeerror_fallback_1781():
    """#1781 back-compat: a legacy plugin whose score_pair LACKS the tf_freqs
    keyword must still score through the resolver when a table is supplied --
    TypeError fallback to the bare call, twinning the score_matrix posture in
    core/scorer.py:594-597. Registered inside the test (xdist self-contained)."""
    from goldenmatch.plugins.registry import PluginRegistry

    class _LegacyNoKwScorer:
        name = "legacy_no_tf_kw_1781"

        def score_pair(self, a, b):
            return 0.42

    PluginRegistry.instance().register_scorer(
        "legacy_no_tf_kw_1781", _LegacyNoKwScorer()
    )
    fn = _resolve_score_pair_callable("legacy_no_tf_kw_1781", {"x": 0.5})
    assert fn is not None
    assert fn("a", "b") == 0.42  # TypeError fallback, no crash
    assert fn("c", "d") == 0.42  # second call still works
