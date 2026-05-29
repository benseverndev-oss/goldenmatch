"""Fast per-pair Fellegi-Sunter scoring (probabilistic_fast.py).

Parity contract: same input through `score_probabilistic_fast` produces
the same pair set + scores (within rapidfuzz tolerance) as the slow path
through `score_probabilistic`, when the gate accepts.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.matchkey import _xform_sig, precompute_matchkey_transforms
from goldenmatch.core.probabilistic import EMResult, score_probabilistic
from goldenmatch.core.probabilistic_fast import (
    _resolve_probabilistic_fast_path,
    score_probabilistic_fast,
)


def _mk_probabilistic(levels: int = 2) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="prob",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=levels, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=levels, partial_threshold=0.8),
        ],
        link_threshold=0.5,
    )


def _em_2level() -> EMResult:
    return EMResult(
        m_probs={"first_name": [0.1, 0.9], "last_name": [0.05, 0.95]},
        u_probs={"first_name": [0.95, 0.05], "last_name": [0.97, 0.03]},
        # log2(m/u) per level. agree weights > 0; disagree weights < 0.
        match_weights={"first_name": [-3.25, 4.17], "last_name": [-4.27, 4.98]},
        converged=True,
        iterations=5,
        proportion_matched=0.1,
    )


def _em_3level() -> EMResult:
    return EMResult(
        m_probs={"first_name": [0.05, 0.20, 0.75], "last_name": [0.03, 0.15, 0.82]},
        u_probs={"first_name": [0.80, 0.15, 0.05], "last_name": [0.85, 0.12, 0.03]},
        match_weights={"first_name": [-4.0, 0.41, 3.91], "last_name": [-4.82, 0.32, 4.77]},
        converged=True,
        iterations=5,
        proportion_matched=0.1,
    )


def _prepared(first_names: list[str], last_names: list[str]) -> pl.DataFrame:
    df = pl.DataFrame({
        "__row_id__": list(range(len(first_names))),
        "first_name": first_names,
        "last_name": last_names,
    })
    return precompute_matchkey_transforms(df, [_mk_probabilistic()])


class TestResolveProbabilisticFastPath:
    def test_accepts_2level(self):
        mk = _mk_probabilistic(levels=2)
        df = _prepared(["alice", "alice"], ["smith", "smith"])
        spec = _resolve_probabilistic_fast_path(mk, df, _em_2level())
        assert spec is not None

    def test_accepts_3level(self):
        mk = _mk_probabilistic(levels=3)
        df = _prepared(["alice", "alice"], ["smith", "smith"])
        spec = _resolve_probabilistic_fast_path(mk, df, _em_3level())
        assert spec is not None

    def test_rejects_weighted_matchkey(self):
        mk = MatchkeyConfig(
            name="w", type="weighted", threshold=0.5,
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0)],
        )
        df = _prepared(["a", "a"], ["b", "b"])
        assert _resolve_probabilistic_fast_path(mk, df, _em_2level()) is None

    def test_rejects_missing_xform(self):
        """xform_sig not in df.columns -> fall back."""
        mk = _mk_probabilistic(levels=2)
        # Don't run precompute_matchkey_transforms.
        bare = pl.DataFrame({"__row_id__": [0, 1], "first_name": ["a", "a"], "last_name": ["b", "b"]})
        assert _resolve_probabilistic_fast_path(mk, bare, _em_2level()) is None

    def test_rejects_model_backed_scorer(self):
        mk = MatchkeyConfig(
            name="prob", type="probabilistic",
            fields=[
                MatchkeyField(field="x", scorer="embedding", levels=2, partial_threshold=0.8),
            ],
            link_threshold=0.5,
        )
        df = pl.DataFrame({"__row_id__": [0, 1], "x": ["a", "a"]})
        # Add xform col so the missing-xform gate doesn't beat the scorer one.
        df = df.with_columns(pl.col("x").alias(_xform_sig(mk.fields[0])))
        assert _resolve_probabilistic_fast_path(mk, df, _em_2level()) is None

    def test_rejects_levels_4(self):
        mk = MatchkeyConfig(
            name="prob", type="probabilistic",
            fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", levels=4, partial_threshold=0.8),
            ],
            link_threshold=0.5,
        )
        df = _prepared(["a", "a"], ["b", "b"])
        em = EMResult(
            m_probs={"first_name": [0.05, 0.1, 0.2, 0.65]},
            u_probs={"first_name": [0.8, 0.1, 0.05, 0.05]},
            match_weights={"first_name": [-4.0, 0.0, 2.0, 3.7]},
            converged=True, iterations=5, proportion_matched=0.1,
        )
        assert _resolve_probabilistic_fast_path(mk, df, em) is None


class TestParityWithSlowPath:
    """Pair set + scores from fast path must equal those from slow path
    on representative blocks. Source of truth is `score_probabilistic`."""

    @pytest.mark.parametrize("levels", [2, 3])
    def test_parity_matching_pair(self, levels):
        mk = _mk_probabilistic(levels=levels)
        em = _em_2level() if levels == 2 else _em_3level()
        # Two identical pairs + one disagree pair.
        df = _prepared(
            ["alice", "alice", "bob"],
            ["smith", "smith", "jones"],
        )
        fast_spec = _resolve_probabilistic_fast_path(mk, df, em)
        assert fast_spec is not None
        fast_pairs = score_probabilistic_fast(df, fast_spec)
        slow_pairs = score_probabilistic(df, mk, em)
        fast_keys = sorted((a, b) for a, b, _ in fast_pairs)
        slow_keys = sorted((a, b) for a, b, _ in slow_pairs)
        assert fast_keys == slow_keys, (
            f"pair-set mismatch:\n  fast={fast_pairs}\n  slow={slow_pairs}"
        )
        # Score parity within 0.01 (rounding + rapidfuzz noise).
        for (fa, fb, fs), (sa, sb, ss) in zip(sorted(fast_pairs), sorted(slow_pairs)):
            assert (fa, fb) == (sa, sb)
            assert pytest.approx(fs, abs=0.01) == ss, (
                f"score mismatch at ({fa},{fb}): fast={fs} slow={ss}"
            )

    def test_parity_disagree_below_threshold(self):
        """Pair with disagreement should produce identical "not emitted"
        decision on both paths."""
        mk = _mk_probabilistic(levels=2)
        em = _em_2level()
        df = _prepared(["alice", "zzzzz"], ["smith", "ttttt"])
        fast_spec = _resolve_probabilistic_fast_path(mk, df, em)
        assert fast_spec is not None
        fast_pairs = score_probabilistic_fast(df, fast_spec)
        slow_pairs = score_probabilistic(df, mk, em)
        assert sorted((a, b) for a, b, _ in fast_pairs) == sorted((a, b) for a, b, _ in slow_pairs)


class TestExcludePairs:
    def test_exclude_filters_pair(self):
        mk = _mk_probabilistic(levels=2)
        em = _em_2level()
        df = _prepared(["alice", "alice"], ["smith", "smith"])
        fast_spec = _resolve_probabilistic_fast_path(mk, df, em)
        assert fast_spec is not None
        # Without exclude: should emit (0, 1).
        pairs_no_exclude = score_probabilistic_fast(df, fast_spec)
        assert len(pairs_no_exclude) == 1
        # With exclude: should emit nothing.
        pairs_with_exclude = score_probabilistic_fast(df, fast_spec, exclude_pairs={(0, 1)})
        assert pairs_with_exclude == []
