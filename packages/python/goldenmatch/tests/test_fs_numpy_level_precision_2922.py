"""#2922: the numpy FS path banded similarities in float32, the kernel in float64.

Jaro-Winkler('anderson', 'andersen') is exactly 0.95, the 3-level top cut. The
kernel computes it in f64 and places the pair at level 2. The numpy path built its
similarity matrix in float32 (the weighted matcher's memory-saving dtype), stored
0.95 as 0.949999988079071, lost ``>= 0.95``, and placed it at level 1: score 1.0
natively, 0.5773 on numpy. On the zero-config panel that moved 58 predicted pairs
on historical_50k with TF off. First filed as a TF bug; TF was incidental.
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField
from goldenmatch.core import probabilistic as P

# The ACTUAL measured pair and value, not stand-ins.
PAIR = ["anderson", "andersen"]
FLOAT32_SLACK = 0.949999988079071


def test_known_positive_the_pair_really_sits_on_the_cut():
    """If this stops being exactly 0.95, every test below tests nothing."""
    from goldenmatch.core.strsim import jaro_winkler_normalized_similarity as jw

    assert jw(*PAIR) == 0.95


def test_known_positive_the_raw_matrix_really_is_truncated():
    """Without this, the repair tests could pass against a matrix that was never broken."""
    raw = P._field_score_matrix(PAIR, "jaro_winkler")
    assert float(raw[0][1]) == pytest.approx(FLOAT32_SLACK, abs=1e-12)


def test_a_cell_on_a_cut_is_recomputed_exactly():
    sim = P._field_score_matrix_dedup(PAIR, "jaro_winkler", cuts=[0.8, 0.95])
    assert float(sim[0][1]) == 0.95


def test_a_cell_off_every_cut_keeps_its_fast_value():
    """The repair is targeted: away from every cut, the float32 value stands."""
    sim = P._field_score_matrix_dedup(PAIR, "jaro_winkler", cuts=[0.8])
    assert float(sim[0][1]) == pytest.approx(FLOAT32_SLACK, abs=1e-12)


@pytest.mark.parametrize(
    "levels,pt,thresholds,expected",
    [(3, 0.8, None, 2), (2, 0.95, None, 1), (4, 0.8, [0.5, 0.95], 2)],
)
def test_cuts_cover_every_banding_mode(levels, pt, thresholds, expected):
    """3-level top cut, 2-level partial cut, and custom level_thresholds."""
    cuts = P._similarity_cuts(levels, pt, thresholds)
    sim = P._field_score_matrix_dedup(PAIR, "jaro_winkler", cuts=cuts)
    got = P._levels_from_similarity(sim, levels, pt, level_thresholds=thresholds)
    assert int(got[0][1]) == expected


@pytest.mark.parametrize("threshold,fires", [(0.96, True), (0.95, False)])
def test_negative_evidence_does_not_fire_on_float32_slack(threshold, fires):
    """NE fires on similarity STRICTLY below its threshold. At 0.95 exactly the
    kernel does not fire; float32 0.949999 did. The 0.96 case is the
    KNOWN-POSITIVE: it proves this test can observe a fire at all."""
    ne = NegativeEvidenceField(field="surname", scorer="jaro_winkler", threshold=threshold, penalty_bits=5.0)
    tw = np.zeros((2, 2))
    P._add_ne_matrix_contribution(tw, list(PAIR), ne, _em())
    assert bool(tw[0, 1] == -5.0) is fires  # bool(): numpy True_ is not True


def _df():
    return pl.DataFrame({"__row_id__": [0, 1], "surname": PAIR, "block": ["b", "b"]})


def _mk():
    return MatchkeyConfig(
        name="fs", type="probabilistic", link_threshold=0.0,
        fields=[MatchkeyField(field="surname", scorer="jaro_winkler", levels=3, partial_threshold=0.8)],
    )


def _em():
    return P.EMResult(
        m_probs={"surname": [0.05, 0.15, 0.8]},
        u_probs={"surname": [0.85, 0.1, 0.05]},
        match_weights={"surname": [-4.09, 0.58, 4.0]},
        converged=True, iterations=1, proportion_matched=0.1,
    )


def _score(pairs):
    return {(min(a, b), max(a, b)): s for a, b, s in pairs}


def test_vectorized_route_scores_the_pair_like_the_scalar_route(monkeypatch):
    """The scalar route bands with ``score_field`` in f64 -- the levels EM trains
    on, and the result the native kernel gives (1.0, measured)."""
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    df, mk, em = _df(), _mk(), _em()
    ref = _score(P.score_probabilistic(df, mk, em))
    assert ref == {(0, 1): 1.0}
    assert _score(P.score_probabilistic_vectorized(df, mk, em)) == ref


def test_batched_route_scores_the_pair_like_the_scalar_route(monkeypatch):
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks

    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", "1")
    df, mk, em = _df(), _mk(), _em()
    blocks = build_blocks(
        df.lazy(), BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["block"])]),
    )
    assert _score(P.score_probabilistic_blocks_batched(blocks, mk, em, set())) == {(0, 1): 1.0}
