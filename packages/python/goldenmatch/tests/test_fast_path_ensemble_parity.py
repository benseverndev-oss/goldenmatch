"""Parity guard: the probabilistic FAST path handles `scorer="ensemble"`.

The probabilistic fast path (`core/probabilistic_fast.py`) used to disqualify
whenever any field used `ensemble`, forcing the O(block^2) Python slow path.
This test pins the fix: with an ensemble field, `_resolve_probabilistic_fast_path`
must resolve a NON-None spec, and `score_probabilistic_fast` must produce results
identical (pair set + score) to the slow `score_probabilistic` path.

This is safe because BOTH the prob-fast and prob-slow paths share the SAME
per-pair ensemble definition (`core/scorer.py::_ensemble_score_single`, reached
via `score_field(a, b, "ensemble")` on the slow path). We are matching
prob-fast to prob-slow, NOT to the matrix path used by the weighted/bucket
backend (whose deliberate ensemble decline in
`backends/score_buckets.py::_resolve_score_pair_callable` is unchanged).
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.matchkey import precompute_matchkey_transforms
from goldenmatch.core.probabilistic import score_probabilistic, train_em
from goldenmatch.core.probabilistic_fast import (
    _resolve_probabilistic_fast_path,
    score_probabilistic_fast,
)


def _prep(df: pl.DataFrame, mk: MatchkeyConfig) -> pl.DataFrame:
    """Materialize the __xform_<sig>__ columns the fast path's gate requires.

    Mirrors what `core/pipeline.py` does before probabilistic scoring: it calls
    `precompute_matchkey_transforms(collected_df, matchkeys)` (pipeline.py line
    1097-1098) to add one `__xform_<sig>__` column per unique (field, transforms)
    signature. `_resolve_probabilistic_fast_path` checks `_xform_sig(f) in
    prepared_df.columns`, so this is the real, in-tree prep helper.
    """
    return precompute_matchkey_transforms(df, [mk])


def test_prob_fast_ensemble_matches_slow():
    df = pl.DataFrame({
        "__row_id__": list(range(8)),
        "name": ["jon smith", "smith jon", "jonathan smith", "jon smyth",
                 "alice ng", "alice ng", "bob lee", "bobby lee"],
        "city": ["nyc", "nyc", "nyc", "nyc", "la", "la", "sf", "sf"],
    })
    mk = MatchkeyConfig(name="p", type="probabilistic", fields=[
        MatchkeyField(field="name", scorer="ensemble", levels=3, partial_threshold=0.8),
        MatchkeyField(field="city", scorer="exact", levels=2, partial_threshold=0.9),
    ])
    em = train_em(df, mk, n_sample_pairs=50)
    prepared = _prep(df, mk)
    spec = _resolve_probabilistic_fast_path(mk, prepared, em)
    assert spec is not None, "ensemble must NOT disqualify the probabilistic fast path"
    fast = sorted(score_probabilistic_fast(prepared, spec))
    slow = sorted(score_probabilistic(df, mk, em))
    assert len(fast) == len(slow)
    for (a1, b1, s1), (a2, b2, s2) in zip(fast, slow):
        assert (a1, b1) == (a2, b2)
        assert abs(s1 - s2) < 1e-6
