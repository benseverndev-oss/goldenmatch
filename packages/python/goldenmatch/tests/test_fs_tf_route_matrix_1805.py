"""Issue #1805 (checkbox 1) — term-frequency (Winkler) adjustment must apply
IDENTICALLY on every FS scoring route, not just the two #1801 pinned.

#1801 fixed TF on the scalar path and pinned scalar == vectorized
(`test_fs_tf_scalar_parity_1801.py`). But TF was only ever *directly* tested on
those two routes (plus a skipif-gated native case). The **batched**
(`score_probabilistic_blocks_batched`) and **bucket** (`score_buckets`) routes
had NO TF test — a route silently dropping the `tf_adjustment` weight, exactly
the #1801 class of bug, would score the same config differently and go uncaught.

This is the route-vs-route parity matrix the audit (FS_PIPELINE_ANALYSIS_2026-07-15)
asked for: the vectorized route is the reference, and every other route must
reproduce its per-pair TF-adjusted scores on one identical config. The
non-native routes (scalar / vectorized / batched-vec / batched-scalar /
bucket-python) run unconditionally; native and bucket-native ride the existing
`_fs_native_enabled()` skipif so this file stays green on a pure-Python install.
"""
from __future__ import annotations

from collections import Counter

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.probabilistic import (
    EMResult,
    _fs_native_enabled,
    score_probabilistic,
    score_probabilistic_blocks_batched,
    score_probabilistic_vectorized,
)

native_required = pytest.mark.skipif(
    not _fs_native_enabled(),
    reason="native FS kernel not built/enabled (GOLDENMATCH_FS_NATIVE + built _native)",
)


# ── Fixtures: one block of surnames skewed common/mid/rare, exact-scored ──────


def _surname_df() -> pl.DataFrame:
    """11 rows in ONE block: "smith" common (6), "jones" mid (3),
    "zelinski" rare (2). A constant ``block`` column keeps every row in a single
    block so the blocking routes (batched/bucket) compare the SAME pair universe
    the whole-frame routes (scalar/vectorized) do."""
    names = ["smith"] * 6 + ["jones"] * 3 + ["zelinski"] * 2
    return pl.DataFrame(
        {
            "__row_id__": list(range(len(names))),
            "surname": names,
            "block": ["b"] * len(names),
        }
    )


def _tf_em(df: pl.DataFrame) -> EMResult:
    """A hand-built EM result carrying the TF frequency table for ``surname`` —
    mirrors `test_fs_tf_scalar_parity_1801.py::_tf_em` so the reference weights
    are shared with the #1801 pin."""
    n = df.height
    freqs = {k: v / n for k, v in Counter(df["surname"].to_list()).items()}
    collision = sum(p * p for p in freqs.values())
    return EMResult(
        m_probs={"surname": [0.05, 0.95]},
        u_probs={"surname": [0.9, 0.1]},
        match_weights={"surname": [-4.0, 3.0]},
        converged=True, iterations=5, proportion_matched=0.1,
        tf_freqs={"surname": freqs}, tf_collision={"surname": collision},
    )


def _tf_mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic", link_threshold=0.0,
        fields=[MatchkeyField(
            field="surname", scorer="exact", levels=2, tf_adjustment=True,
        )],
    )


def _blocking() -> BlockingConfig:
    return BlockingConfig(
        strategy="static", keys=[BlockingKeyConfig(fields=["block"])],
    )


def _scores(pairs) -> dict[tuple[int, int], float]:
    return {(min(a, b), max(a, b)): round(s, 9) for a, b, s in pairs}


# ── Per-route scorers (all consume the SAME df/mk/em) ─────────────────────────


def _route_scalar(df, mk, em):
    return _scores(score_probabilistic(df, mk, em))


def _route_vectorized(df, mk, em):
    return _scores(score_probabilistic_vectorized(df, mk, em))


def _route_batched(df, mk, em, monkeypatch, *, vectorized: bool):
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", "1" if vectorized else "0")
    blocks = build_blocks(df.lazy(), _blocking())
    return _scores(score_probabilistic_blocks_batched(blocks, mk, em, set()))


def _route_bucket(df, mk, em, monkeypatch, *, native: bool):
    from goldenmatch.backends.score_buckets import score_buckets

    monkeypatch.setenv("GOLDENMATCH_FS_BUCKET_NATIVE", "1" if native else "0")
    return _scores(score_buckets(df, _blocking(), mk, set(), em_result=em))


# ── The reference: vectorized (the route #1801 proved correct) ────────────────


def test_reference_vectorized_applies_tf():
    """Anchor: on the reference route a rare exact agreement outscores a common
    one, and BOTH are emitted (so a TF-dropping route can't hide by emitting the
    same pair set with flat scores)."""
    df, mk = _surname_df(), _tf_mk()
    vec = _route_vectorized(df, mk, _tf_em(df))
    assert (0, 1) in vec and (9, 10) in vec  # smith-smith and zelinski-zelinski
    assert vec[(9, 10)] > vec[(0, 1)], "reference route must reward rarity"


# ── Route-vs-route TF parity: every route == the vectorized reference ─────────


def test_scalar_matches_vectorized_on_tf():
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    assert _route_scalar(df, mk, em) == _route_vectorized(df, mk, em)


def test_batched_vec_matches_vectorized_on_tf(monkeypatch):
    """The batched vectorized lane must carry TF through the SxS batch scorer."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    ref = _route_vectorized(df, mk, em)
    got = _route_batched(df, mk, em, monkeypatch, vectorized=True)
    assert got == ref


def test_batched_scalar_matches_vectorized_on_tf(monkeypatch):
    """The batched per-block (scalar) lane must carry TF too — this is the lane
    a model-backed / native-ineligible scorer falls onto."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    ref = _route_vectorized(df, mk, em)
    got = _route_batched(df, mk, em, monkeypatch, vectorized=False)
    assert got == ref


def test_bucket_python_matches_vectorized_on_tf(monkeypatch):
    """The bucket route's Python per-block scorer (the default when the planner
    picks bucket but no native kernel is present) must apply TF identically."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    ref = _route_vectorized(df, mk, em)
    got = _route_bucket(df, mk, em, monkeypatch, native=False)
    assert got == ref


# ── Native routes (skipif-gated so the file is green on pure-Python) ──────────


@native_required
def test_native_matches_vectorized_on_tf():
    from goldenmatch.core.probabilistic import score_probabilistic_native

    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    ref = _route_vectorized(df, mk, em)
    got = _scores(score_probabilistic_native(df, mk, em))
    assert got == ref


@native_required
def test_bucket_native_matches_vectorized_on_tf(monkeypatch):
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    ref = _route_vectorized(df, mk, em)
    got = _route_bucket(df, mk, em, monkeypatch, native=True)
    assert got == ref


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
