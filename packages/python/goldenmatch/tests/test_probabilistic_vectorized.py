"""Vectorized Fellegi-Sunter block scoring + score calibration.

Two contracts under test:

1. Parity: `score_probabilistic_vectorized` produces the same pair set + scores
   (within rapidfuzz/native-kernel tolerance) as the scalar `score_probabilistic`.
2. Calibration: `GOLDENMATCH_FS_CALIBRATED=posterior` turns the score into a
   true match probability (uses the EM prior, value in (0,1), monotonic in W)
   while the default `linear` mode is byte-identical to the historical behavior.
"""
from __future__ import annotations

import math

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    EMResult,
    _fs_vec_guard,
    _fs_vec_max_elems,
    posterior_from_weight,
    prior_weight,
    probabilistic_block_scorer,
    score_probabilistic,
    score_probabilistic_vectorized,
    train_em,
    vectorized_scorer_supported,
)


class TestVecMatrixGuard:
    """#1826/#1857: the dense-matrix guard must refuse an oversized block BEFORE
    the multi-matrix, parallel composition OOMs the host."""

    def test_default_is_scale_realistic(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", raising=False)
        # Tightened from 2e9 to 5e7 (n~7,071) to account for ~6 matrices/block
        # scored across a <=16-thread pool.
        assert _fs_vec_max_elems() == 50_000_000

    def test_guard_refuses_oversized_block(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", raising=False)
        # ~11K-row block (the surname-soundex shape at 1M) exceeds the cap.
        with pytest.raises(ValueError, match="refusing"):
            _fs_vec_guard(11_000, "score_probabilistic_vectorized")
        # A block under the cap is allowed (no raise).
        _fs_vec_guard(5_000, "score_probabilistic_vectorized")

    def test_env_override_and_disable(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", "100")
        assert _fs_vec_max_elems() == 100
        with pytest.raises(ValueError):
            _fs_vec_guard(11, "x")  # 11*11=121 > 100
        monkeypatch.setenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", "0")  # disabled
        _fs_vec_guard(10_000_000, "x")  # no raise


def _df():
    return pl.DataFrame({
        "__row_id__": list(range(1, 13)),
        "first_name": ["John", "Jon", "Jane", "Janet", "Bob", "Robert",
                       "Alice", "Alicia", "Tom", "Thomas", "Kate", "Katie"],
        "last_name": ["Smith", "Smith", "Doe", "Doe", "Jones", "Jones",
                      "Brown", "Brown", "Wilson", "Wilson", "Green", "Green"],
        "zip": ["90210", "90210", "10001", "10001", "60601", "60601",
                "30301", "30301", "20001", "20002", "44114", "44114"],
    })


def _mk(**kw):
    defaults = dict(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    defaults.update(kw)
    return MatchkeyConfig(**defaults)


def _pairset(pairs):
    return {(min(a, b), max(a, b)): round(s, 3) for a, b, s in pairs}


# ── Calibration math ────────────────────────────────────────────────────────


class TestCalibrationMath:
    def test_prior_weight_sign(self):
        # Rare matches -> strongly negative prior log-odds.
        assert prior_weight(0.002) < -8
        # Even prior -> 0 bits.
        assert prior_weight(0.5) == pytest.approx(0.0, abs=1e-9)
        # Common matches -> positive.
        assert prior_weight(0.9) > 0

    def test_prior_weight_clamps_extremes(self):
        assert math.isfinite(prior_weight(0.0))
        assert math.isfinite(prior_weight(1.0))

    def test_posterior_is_probability(self):
        p = posterior_from_weight(0.0, 0.0)
        assert p == pytest.approx(0.5)
        assert 0.0 <= posterior_from_weight(-1000, -50) <= 1.0
        assert posterior_from_weight(1000, 0) == 1.0
        assert posterior_from_weight(-1000, 0) == 0.0

    def test_posterior_monotonic_in_weight(self):
        prior = prior_weight(0.01)
        vals = [posterior_from_weight(w, prior) for w in range(-5, 30, 2)]
        assert all(b >= a for a, b in zip(vals, vals[1:]))


# ── Vectorized parity ─────────────────────────────────────────────────────────


class TestVectorizedParity:
    def test_matches_scalar_pairset(self):
        df = _df()
        mk = _mk()
        em = train_em(df, mk, n_sample_pairs=200)
        slow = _pairset(score_probabilistic(df, mk, em))
        vec = _pairset(score_probabilistic_vectorized(df, mk, em))
        # Identical pair set on this clean synthetic block.
        assert set(slow) == set(vec)
        for k in slow:
            assert slow[k] == pytest.approx(vec[k], abs=0.01)

    def test_parity_with_transforms(self):
        df = _df().with_columns(pl.col("first_name").str.to_uppercase())
        mk = _mk(fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.8, transforms=["lowercase"]),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ])
        em = train_em(df, mk, n_sample_pairs=200)
        slow = _pairset(score_probabilistic(df, mk, em))
        vec = _pairset(score_probabilistic_vectorized(df, mk, em))
        assert set(slow) == set(vec)

    def test_parity_with_nulls(self):
        df = _df()
        df[0, "first_name"] = None
        mk = _mk()
        em = train_em(df, mk, n_sample_pairs=200)
        slow = _pairset(score_probabilistic(df, mk, em))
        vec = _pairset(score_probabilistic_vectorized(df, mk, em))
        assert set(slow) == set(vec)

    def test_exclude_pairs_honored(self):
        df = _df()
        mk = _mk()
        em = train_em(df, mk, n_sample_pairs=200)
        allp = score_probabilistic_vectorized(df, mk, em)
        assert allp, "expected at least one pair"
        drop = (min(allp[0][0], allp[0][1]), max(allp[0][0], allp[0][1]))
        kept = score_probabilistic_vectorized(df, mk, em, exclude_pairs={drop})
        kept_keys = {(min(a, b), max(a, b)) for a, b, _ in kept}
        assert drop not in kept_keys

    def test_singleton_block_empty(self):
        df = _df().head(1)
        mk = _mk()
        em = train_em(_df(), mk, n_sample_pairs=200)
        assert score_probabilistic_vectorized(df, mk, em) == []

    def test_n_level_field(self):
        df = _df()
        mk = _mk(fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=5),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ])
        em = train_em(df, mk, n_sample_pairs=200)
        slow = _pairset(score_probabilistic(df, mk, em))
        vec = _pairset(score_probabilistic_vectorized(df, mk, em))
        assert set(slow) == set(vec)


# ── Posterior vs linear scoring ───────────────────────────────────────────────


class TestPosteriorMode:
    def test_posterior_scores_in_unit_interval(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
        df = _df()
        mk = _mk(link_threshold=0.0)  # keep everything so we can inspect scores
        em = train_em(df, mk, n_sample_pairs=200)
        pairs = score_probabilistic_vectorized(df, mk, em)
        assert pairs
        assert all(0.0 <= s <= 1.0 for _, _, s in pairs)

    def test_linear_default_unaffected_by_env_absence(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_CALIBRATED", raising=False)
        df = _df()
        mk = _mk()
        em = train_em(df, mk, n_sample_pairs=200)
        # Default mode is linear: vectorized == scalar pairset.
        slow = _pairset(score_probabilistic(df, mk, em))
        vec = _pairset(score_probabilistic_vectorized(df, mk, em))
        assert set(slow) == set(vec)

    def test_posterior_parity_vector_vs_scalar(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
        df = _df()
        mk = _mk(link_threshold=0.5)
        em = train_em(df, mk, n_sample_pairs=200)
        slow = _pairset(score_probabilistic(df, mk, em))
        vec = _pairset(score_probabilistic_vectorized(df, mk, em))
        assert set(slow) == set(vec)


# ── Scorer selection ──────────────────────────────────────────────────────────


class TestTermFrequencyAdjustment:
    """Rare exact agreements carry more match weight than common ones."""

    def _em_surname(self, tf_freqs=None, tf_collision=None):
        return EMResult(
            m_probs={"surname": [0.05, 0.95]},
            u_probs={"surname": [0.9, 0.1]},
            match_weights={"surname": [-4.0, 3.0]},
            converged=True, iterations=5, proportion_matched=0.1,
            tf_freqs=tf_freqs, tf_collision=tf_collision,
        )

    def _df_surnames(self):
        # "smith" common (6), "zelinski" rare (2).
        names = ["smith"] * 6 + ["zelinski"] * 2
        return pl.DataFrame({"__row_id__": list(range(len(names))), "surname": names})

    def test_rare_agreement_outscores_common(self):
        df = self._df_surnames()
        n = df.height
        freqs = {"smith": 6 / n, "zelinski": 2 / n}
        collision = sum(p * p for p in freqs.values())
        mk = MatchkeyConfig(
            name="fs", type="probabilistic", link_threshold=0.0,
            fields=[MatchkeyField(field="surname", scorer="exact", levels=2, tf_adjustment=True)],
        )
        em = self._em_surname({"surname": freqs}, {"surname": collision})
        scores = {(min(a, b), max(a, b)): s for a, b, s in
                  score_probabilistic_vectorized(df, mk, em)}
        # smith-smith pair (0,1) vs zelinski-zelinski pair (6,7)
        assert scores[(6, 7)] > scores[(0, 1)], "rare-name agreement should score higher"

    def test_no_tf_table_is_noop(self):
        df = self._df_surnames()
        mk = MatchkeyConfig(
            name="fs", type="probabilistic", link_threshold=0.0,
            fields=[MatchkeyField(field="surname", scorer="exact", levels=2, tf_adjustment=True)],
        )
        em = self._em_surname(tf_freqs=None)  # EM produced no table
        scores = {(min(a, b), max(a, b)): s for a, b, s in
                  score_probabilistic_vectorized(df, mk, em)}
        # Without a TF table, common and rare agreements score identically.
        assert scores[(6, 7)] == pytest.approx(scores[(0, 1)])

    def test_train_em_builds_tf_table_only_when_opted_in(self):
        df = self._df_surnames().with_columns(pl.lit("x").alias("zip"))
        mk_on = MatchkeyConfig(
            name="fs", type="probabilistic",
            fields=[MatchkeyField(field="surname", scorer="exact", levels=2, tf_adjustment=True)],
        )
        mk_off = MatchkeyConfig(
            name="fs", type="probabilistic",
            fields=[MatchkeyField(field="surname", scorer="exact", levels=2)],
        )
        em_on = train_em(df, mk_on, n_sample_pairs=50)
        em_off = train_em(df, mk_off, n_sample_pairs=50)
        assert em_on.tf_freqs is not None and "surname" in em_on.tf_freqs
        assert em_off.tf_freqs is None


class TestBlockScorerSelection:
    def test_selects_vectorized_for_matrix_scorers(self):
        mk = _mk()
        em = train_em(_df(), mk, n_sample_pairs=200)
        fn = probabilistic_block_scorer(mk, em)
        # Should produce the same as a direct vectorized call.
        assert _pairset(fn(_df())) == _pairset(score_probabilistic_vectorized(_df(), mk, em))

    def test_env_kill_switch_forces_scalar(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_VECTORIZED", "0")
        mk = _mk()
        em = train_em(_df(), mk, n_sample_pairs=200)
        fn = probabilistic_block_scorer(mk, em)
        assert _pairset(fn(_df())) == _pairset(score_probabilistic(_df(), mk, em))

    def test_embedding_scorers_are_vectorizable(self):
        # #1806: embedding / record_embedding are now first-class on the
        # vectorized FS path (they were matrix-only and previously excluded,
        # which forced the crashing scalar path).
        assert vectorized_scorer_supported("jaro_winkler")
        assert vectorized_scorer_supported("exact")
        assert vectorized_scorer_supported("embedding")
        assert vectorized_scorer_supported("record_embedding")


class TestRequirePositiveEvidence:
    """Net-zero-evidence filter (GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE):
    a linear-mode pair whose summed match weight (LR) is <= 0 must NOT be emitted
    when the flag is on, so the asymmetric min-max can't auto-link net-zero pairs
    into mega-clusters. Positive-evidence pairs are unaffected. See the design
    spec docs/superpowers/specs/2026-07-18-fs-net-zero-evidence-filter.md."""

    def _em(self):
        # Asymmetric weights (disagree -3.0, agree +1.0): a pair agreeing on ONE
        # field and disagreeing on the other sums to -2.0 (<=0, net-negative
        # evidence) yet the min-max range [-6, +2] maps -2.0 onto EXACTLY 0.50 --
        # so it clears a 0.50 link cut. That is the over-merge the filter kills.
        return EMResult(
            m_probs={"name": [0.1, 0.9], "zip": [0.05, 0.95]},
            u_probs={"name": [0.9, 0.1], "zip": [0.95, 0.05]},
            match_weights={"name": [-3.0, 1.0], "zip": [-3.0, 1.0]},
            converged=True, iterations=5, proportion_matched=0.1,
        )

    def _df(self):
        # rows 0,1 share zip (block key) but disagree on name -> net-zero pair.
        # rows 2,3 agree on BOTH name and zip -> strong positive evidence.
        return pl.DataFrame({
            "__row_id__": [0, 1, 2, 3],
            "name": ["alice", "bob", "carol", "carol"],
            "zip": ["10001", "10001", "20002", "20002"],
        })

    def _mk(self):
        return MatchkeyConfig(
            name="fs", type="probabilistic",
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
                MatchkeyField(field="zip", scorer="exact", levels=2),
            ],
        )

    def test_default_on_drops_net_zero_pair(self, monkeypatch):
        # Default ON: the net-zero pair is filtered without any env var set.
        monkeypatch.delenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", raising=False)
        pairs = score_probabilistic_vectorized(self._df(), self._mk(), self._em())
        keys = {(min(a, b), max(a, b)) for a, b, _ in pairs}
        assert (0, 1) not in keys  # net-zero pair filtered by default
        assert (2, 3) in keys      # positive pair emitted

    def test_explicit_off_emits_net_zero_pair(self, monkeypatch):
        # =0 restores the legacy emit-at-neutral behavior.
        monkeypatch.setenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", "0")
        pairs = score_probabilistic_vectorized(self._df(), self._mk(), self._em())
        keys = {(min(a, b), max(a, b)) for a, b, _ in pairs}
        assert (0, 1) in keys  # net-zero pair emitted (legacy)
        assert (2, 3) in keys

    def test_on_drops_net_zero_keeps_positive(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", "1")
        pairs = score_probabilistic_vectorized(self._df(), self._mk(), self._em())
        keys = {(min(a, b), max(a, b)) for a, b, _ in pairs}
        assert (0, 1) not in keys  # net-zero pair filtered (the over-merge cause)
        assert (2, 3) in keys      # positive evidence preserved (recall-safe)

    def test_scalar_matches_vectorized_when_on(self, monkeypatch):
        # The scalar and vectorized emit paths apply the filter identically.
        monkeypatch.setenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", "1")
        df, mk, em = self._df(), self._mk(), self._em()
        vec = {(min(a, b), max(a, b)) for a, b, _ in score_probabilistic_vectorized(df, mk, em)}
        sca = {(min(a, b), max(a, b)) for a, b, _ in score_probabilistic(df, mk, em)}
        assert vec == sca
        assert (0, 1) not in vec

    def test_posterior_unaffected(self, monkeypatch):
        # Posterior calibration already handles the prior via the 0.99 Bayes cut;
        # the filter is linear-only, so posterior output is unchanged by the flag.
        monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
        df, mk, em = self._df(), self._mk(), self._em()
        monkeypatch.setenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", "0")
        off = {(min(a, b), max(a, b)) for a, b, _ in score_probabilistic_vectorized(df, mk, em)}
        monkeypatch.setenv("GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE", "1")
        on = {(min(a, b), max(a, b)) for a, b, _ in score_probabilistic_vectorized(df, mk, em)}
        assert off == on
