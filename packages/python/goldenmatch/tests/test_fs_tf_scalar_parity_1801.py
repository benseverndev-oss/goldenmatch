"""Issue #1801 — term-frequency (Winkler) adjustment must apply on the
scalar FS path too, not only the vectorized path.

`tf_adjustment=True` was applied by `score_probabilistic_vectorized`
(`_apply_tf_adjustment`) but silently skipped by the scalar
`score_probabilistic` weight loop and `score_pair_probabilistic`, so the
SAME config scored differently depending on the route (scalar is forced by
a model-backed scorer or `GOLDENMATCH_FS_VECTORIZED=0`). These tests pin
scalar==vectorized TF parity, TF on the single-pair path, and a route-parity
check through the block-scorer selector + `dedupe_df`.
"""

from __future__ import annotations

from collections import Counter

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    OutputConfig,
)
from goldenmatch.core.probabilistic import (
    EMResult,
    probabilistic_block_scorer,
    score_pair_probabilistic,
    score_probabilistic,
    score_probabilistic_vectorized,
)


def _surname_df() -> pl.DataFrame:
    # "smith" common (6), "jones" mid (3), "zelinski" rare (2).
    names = ["smith"] * 6 + ["jones"] * 3 + ["zelinski"] * 2
    return pl.DataFrame(
        {"__row_id__": list(range(len(names))), "surname": names},
    )


def _tf_em(df: pl.DataFrame) -> EMResult:
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


def _scores(pairs):
    return {(min(a, b), max(a, b)): s for a, b, s in pairs}


def test_scalar_matches_vectorized_on_tf():
    """The whole point of #1801: identical scores on both routes."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    vec = _scores(score_probabilistic_vectorized(df, mk, em))
    sca = _scores(score_probabilistic(df, mk, em))
    assert sca.keys() == vec.keys()
    for key in vec:
        assert sca[key] == pytest.approx(vec[key], abs=1e-9), key


def test_scalar_tf_rare_agreement_outscores_common():
    """Scalar path must reward the rare-value agreement, like vectorized."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    sca = _scores(score_probabilistic(df, mk, em))
    # zelinski-zelinski (9,10) rarer than smith-smith (0,1).
    assert sca[(9, 10)] > sca[(0, 1)]


def test_scalar_tf_is_noop_without_table():
    """No TF table -> common and rare agreements score identically (scalar)."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)
    em.tf_freqs = None
    em.tf_collision = None
    sca = _scores(score_probabilistic(df, mk, em))
    assert sca[(9, 10)] == pytest.approx(sca[(0, 1)])


def test_score_pair_probabilistic_applies_tf():
    """The single-pair path (match_one) must apply TF: a rare exact agreement
    outscores a common one."""
    df = _surname_df()
    mk = _tf_mk()
    em = _tf_em(df)
    rare = score_pair_probabilistic(
        {"surname": "zelinski"}, {"surname": "zelinski"}, mk, em,
    )
    common = score_pair_probabilistic(
        {"surname": "smith"}, {"surname": "smith"}, mk, em,
    )
    assert rare > common


def test_block_scorer_route_parity_vectorized_vs_scalar(monkeypatch):
    """`probabilistic_block_scorer` must produce identical scores whether it
    routes to the vectorized or scalar path (the seam the bug lived on)."""
    df, mk = _surname_df(), _tf_mk()
    em = _tf_em(df)

    monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", "1")
    vec = _scores(probabilistic_block_scorer(mk, em)(df))
    monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", "0")
    sca = _scores(probabilistic_block_scorer(mk, em)(df))

    assert sca.keys() == vec.keys()
    for key in vec:
        assert sca[key] == pytest.approx(vec[key], abs=1e-9), key


def _tf_config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="fs", type="probabilistic", link_threshold=0.0,
            fields=[MatchkeyField(
                field="surname", scorer="exact", levels=2, tf_adjustment=True,
            )],
        )],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["block"])]),
        output=OutputConfig(),
    )


def _e2e_df() -> pl.DataFrame:
    # One block; rare + common exact-duplicate pairs.
    names = ["smith"] * 6 + ["jones"] * 3 + ["zelinski"] * 2
    return pl.DataFrame({
        "surname": names,
        "block": ["b"] * len(names),
    })


def test_tf_matchkey_e2e_dedupe_df_route_parity(monkeypatch):
    """End-to-end through `dedupe_df` with an explicit TF matchkey: the
    scalar and vectorized routes must yield the same scored pairs."""
    from goldenmatch import dedupe_df

    df = _e2e_df()
    cfg = _tf_config()

    def _pairs(vec_flag: str):
        monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", vec_flag)
        res = dedupe_df(df, config=cfg)
        return _scores(res.scored_pairs)

    vec = _pairs("1")
    sca = _pairs("0")
    assert sca.keys() == vec.keys()
    for key in vec:
        assert sca[key] == pytest.approx(vec[key], abs=1e-9), key


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
