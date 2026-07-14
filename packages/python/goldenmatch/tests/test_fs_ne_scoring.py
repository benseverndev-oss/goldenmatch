"""Task N3: FS negative-evidence scoring contributions + centralized
``fs_weight_range`` + the bucket-backend slim-projection keep-list.

Spec: docs/superpowers/specs/2026-07-14-fs-negative-evidence-design.md
Plan: docs/superpowers/plans/2026-07-14-fs-negative-evidence.md (Task N3)
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core.probabilistic import (
    ContinuousEMResult,
    EMResult,
    _ne_scalar_contribution,
    fs_weight_range,
    score_probabilistic,
    score_probabilistic_continuous,
    score_probabilistic_vectorized,
    train_em,
    train_em_continuous,
)

# ── 1. fs_weight_range ───────────────────────────────────────────────────────


class TestFsWeightRange:
    def test_mixed_regular_ne_learned_and_penalty_bits(self):
        mk = MatchkeyConfig(
            name="mix", type="probabilistic",
            fields=[
                MatchkeyField(field="a", scorer="exact", levels=2),
                MatchkeyField(field="b", scorer="exact", levels=2),
            ],
            negative_evidence=[
                # EM-learned (no penalty_bits) -- uses the __ne__ entry below.
                NegativeEvidenceField(field="ne1", scorer="exact", threshold=1.0),
                # Fixed override, positive bits (abs taken).
                NegativeEvidenceField(field="ne2", scorer="exact", threshold=1.0, penalty_bits=5.0),
                # Fixed override, negative bits (abs taken -- same magnitude).
                NegativeEvidenceField(field="ne3", scorer="exact", threshold=1.0, penalty_bits=-2.5),
            ],
        )
        em = EMResult(
            m_probs={}, u_probs={},
            match_weights={
                "a": [-2.0, 3.0],
                "b": [-1.0, 1.0],
                "__ne__ne1": [-4.0, 0.0],
            },
            converged=True, iterations=0, proportion_matched=0.02,
        )
        min_w, max_w = fs_weight_range(em, mk)
        # min: -2.0 (a) + -1.0 (b) + -4.0 (ne1 learned) + -5.0 (ne2) + -2.5 (ne3)
        assert min_w == pytest.approx(-14.5)
        # max: 3.0 (a) + 1.0 (b) + 0.0 (ne1) + 0.0 (ne2, penalty_bits never adds to max) + 0.0 (ne3)
        assert max_w == pytest.approx(4.0)

    def test_regular_fields_only_unchanged(self):
        mk = MatchkeyConfig(
            name="plain", type="probabilistic",
            fields=[MatchkeyField(field="a", scorer="exact", levels=3)],
        )
        em = EMResult(
            m_probs={}, u_probs={}, match_weights={"a": [-2.0, 0.5, 3.0]},
            converged=True, iterations=0, proportion_matched=0.02,
        )
        assert fs_weight_range(em, mk) == (-2.0, 3.0)

    def test_ne_field_with_neither_penalty_bits_nor_em_entry_contributes_zero(self):
        # Defensive: shouldn't happen post validate_for (N4), but must not KeyError.
        mk = MatchkeyConfig(
            name="defensive", type="probabilistic",
            fields=[MatchkeyField(field="a", scorer="exact", levels=2)],
            negative_evidence=[NegativeEvidenceField(field="ne4", scorer="exact", threshold=1.0)],
        )
        em = EMResult(
            m_probs={}, u_probs={}, match_weights={"a": [-1.0, 1.0]},  # no "__ne__ne4" key
            converged=True, iterations=0, proportion_matched=0.02,
        )
        assert fs_weight_range(em, mk) == (-1.0, 1.0)


# ── 2. Scalar scoring (score_probabilistic) ─────────────────────────────────


def _scalar_mk_and_em(link_threshold: float = 0.0) -> tuple[MatchkeyConfig, EMResult]:
    mk = MatchkeyConfig(
        name="fs_scalar", type="probabilistic",
        fields=[MatchkeyField(field="name", scorer="exact", levels=2)],
        negative_evidence=[NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0)],
        link_threshold=link_threshold,
    )
    em = EMResult(
        m_probs={}, u_probs={},
        match_weights={"name": [-1.0, 2.0], "__ne__phone": [-3.0, 0.0]},
        converged=True, iterations=0, proportion_matched=0.02,
    )
    return mk, em


class TestScalarScoring:
    def _df(self):
        return pl.DataFrame({
            "__row_id__": [1, 2, 3, 4, 5, 6],
            "name": ["a", "a", "b", "b", "c", "c"],
            "phone": ["111", "222", "333", "333", "444", None],
        })

    def test_ne_fires_adds_w_fired(self):
        mk, em = _scalar_mk_and_em()
        pairs = {(a, b): s for a, b, s in score_probabilistic(self._df(), mk, em, set())}
        # name agrees (weight 2.0), phone differs -> NE fires (-3.0) -> total -1.0.
        # min=-1.0-3.0=-4.0, max=2.0+0.0=2.0, range=6.0 -> normalized (−1+4)/6 = 0.5
        assert pairs[(1, 2)] == pytest.approx(0.5)

    def test_ne_not_fired_is_regular_sum_exactly(self):
        mk, em = _scalar_mk_and_em()
        pairs = {(a, b): s for a, b, s in score_probabilistic(self._df(), mk, em, set())}
        # name agrees, phone agrees -> NE not fired -> total = 2.0 exactly -> normalized 1.0
        assert pairs[(3, 4)] == pytest.approx(1.0)

    def test_ne_null_on_one_side_is_regular_sum_exactly(self):
        mk, em = _scalar_mk_and_em()
        pairs = {(a, b): s for a, b, s in score_probabilistic(self._df(), mk, em, set())}
        # name agrees, phone null on row 6 -> NE inconclusive (not fired) -> total = 2.0
        assert pairs[(5, 6)] == pytest.approx(1.0)


# ── 3. penalty_bits fixed override (no __ne__ entry needed) ────────────────


class TestPenaltyBitsFixedOverride:
    def test_fired_contribution_is_exactly_negative_bits(self):
        ne = NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=3.0)
        em = EMResult(
            m_probs={}, u_probs={}, match_weights={"name": [-1.0, 2.0]},  # no __ne__phone
            converged=True, iterations=0, proportion_matched=0.02,
        )
        row_a = {"phone": "111"}
        row_b = {"phone": "999"}
        assert _ne_scalar_contribution(row_a, row_b, ne, em) == -3.0

    def test_negative_penalty_bits_takes_abs(self):
        ne = NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=-3.0)
        em = EMResult(
            m_probs={}, u_probs={}, match_weights={}, converged=True,
            iterations=0, proportion_matched=0.02,
        )
        assert _ne_scalar_contribution({"phone": "111"}, {"phone": "999"}, ne, em) == -3.0

    def test_not_fired_contributes_zero(self):
        ne = NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=3.0)
        em = EMResult(
            m_probs={}, u_probs={}, match_weights={}, converged=True,
            iterations=0, proportion_matched=0.02,
        )
        assert _ne_scalar_contribution({"phone": "111"}, {"phone": "111"}, ne, em) == 0.0


# ── 4. Scalar vs vectorized parity on an NE-bearing matchkey ────────────────


class TestScalarVectorizedParity:
    def _fixture(self):
        # Distinct name groups (soundex-diverse per project fixture rule), each
        # pair member with a DIFFERENT phone -- NE fires on every same-name
        # pair too, exercising the NE math on both scorer paths uniformly.
        names = [
            "Nguyen", "Nguyen", "Okafor", "Okafor", "Petrov", "Petrov",
            "Alvarez", "Alvarez", "Kowalski", "Kowalski", "Haddad", "Haddad",
        ]
        phones = [f"{1_000_000_000 + i}" for i in range(len(names))]
        return pl.DataFrame({
            "__row_id__": list(range(1, len(names) + 1)),
            "name": names,
            "phone": phones,
        })

    def _mk(self):
        return MatchkeyConfig(
            name="fs_parity", type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact", levels=2)],
            negative_evidence=[NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0)],
        )

    def test_identical_pairs_and_scores(self):
        df = self._fixture()
        mk = self._mk()
        em = train_em(df, mk, n_sample_pairs=200)
        assert "__ne__phone" in em.match_weights

        scalar_pairs = sorted(score_probabilistic(df, mk, em, set()))
        vector_pairs = sorted(score_probabilistic_vectorized(df, mk, em, set()))
        assert scalar_pairs == vector_pairs


# ── 5. Normalized scores stay in [0, 1] at the extremes ─────────────────────


class TestNormalizedBounds:
    def test_all_disagree_and_all_fire_normalizes_to_zero_not_negative(self):
        mk = MatchkeyConfig(
            name="fs_extreme", type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact", levels=2)],
            negative_evidence=[NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0)],
            link_threshold=0.0,
        )
        em = EMResult(
            m_probs={}, u_probs={},
            match_weights={"name": [-1.0, 2.0], "__ne__phone": [-3.0, 0.0]},
            converged=True, iterations=0, proportion_matched=0.02,
        )
        df = pl.DataFrame({
            "__row_id__": [1, 2],
            "name": ["a", "b"],       # disagree -> level 0 -> weight -1.0 (the min)
            "phone": ["111", "222"],  # differ -> NE fires -> -3.0 (the min)
        })
        pairs = {(a, b): s for a, b, s in score_probabilistic(df, mk, em, set())}
        score = pairs[(1, 2)]
        assert score == pytest.approx(0.0)
        assert 0.0 <= score <= 1.0


# ── 6. Continuous path rejects NE ────────────────────────────────────────────


class TestContinuousPathRejectsNe:
    def _mk_and_df(self):
        mk = MatchkeyConfig(
            name="fs_cont", type="probabilistic",
            fields=[MatchkeyField(field="a", scorer="exact", levels=2)],
            negative_evidence=[NegativeEvidenceField(field="b", scorer="exact", threshold=1.0)],
        )
        df = pl.DataFrame({"__row_id__": [1, 2], "a": ["x", "x"], "b": ["1", "2"]})
        return mk, df

    def test_train_em_continuous_raises(self):
        mk, df = self._mk_and_df()
        with pytest.raises(ValueError, match="negative_evidence"):
            train_em_continuous(df, mk)

    def test_score_probabilistic_continuous_raises(self):
        mk, df = self._mk_and_df()
        dummy_em = ContinuousEMResult(
            m_mean={"a": 0.9}, m_var={"a": 0.01}, u_mean={"a": 0.2}, u_var={"a": 0.04},
            converged=True, iterations=1, proportion_matched=0.05,
        )
        with pytest.raises(ValueError, match="negative_evidence"):
            score_probabilistic_continuous(df, mk, dummy_em)


# ── 7. Bucket backend: slim-projection keep-list pin (unit level) ──────────


class TestBucketSlimProjectionKeepsNeField:
    def _df(self):
        return pl.DataFrame({
            "__row_id__": [1, 2, 3, 4],
            "name": ["homer", "homer", "homer", "homer"],
            "city": ["springfield"] * 4,
            # row 2's phone differs from everyone else's -- NE should fire
            # only on pairs involving row 2.
            "phone": ["5551111", "5559999", "5551111", "5551111"],
        })

    def _blocking(self):
        return BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])])

    def _em(self):
        return EMResult(
            m_probs={}, u_probs={}, match_weights={"name": [-1.0, 1.0]},
            converged=True, iterations=0, proportion_matched=0.02,
        )

    def test_ne_only_field_fires_through_default_slim_projection(self):
        # Native ENABLED: `_fs_native_eligible` declines NE-bearing matchkeys
        # (N4), so this exercises the real decline routing to the pure-Python
        # path rather than relying on env overrides to isolate the
        # slim-projection fix under test.
        from goldenmatch.backends.score_buckets import score_buckets

        mk_no_ne = MatchkeyConfig(
            name="fs", type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact", levels=2)],
            link_threshold=0.5,
        )
        mk_ne = MatchkeyConfig(
            name="fs", type="probabilistic",
            fields=[MatchkeyField(field="name", scorer="exact", levels=2)],
            negative_evidence=[
                NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0),
            ],
            link_threshold=0.5,
        )
        em = self._em()
        blocking = self._blocking()

        # GOLDENMATCH_BUCKET_SLIM_PROJECTION defaults ON (unset here on purpose
        # -- this is the default-backend pin the spec calls out).
        no_ne_pairs = {(a, b) for a, b, _s in score_buckets(self._df(), blocking, mk_no_ne, set(), em_result=em)}
        ne_pairs = {(a, b) for a, b, _s in score_buckets(self._df(), blocking, mk_ne, set(), em_result=em)}

        assert (1, 2) in no_ne_pairs  # without NE, the differing-phone pair still merges
        assert (1, 2) not in ne_pairs  # with NE (and the keep-list fix), it's suppressed


# ── 8. Default-backend E2E: NE-only field survives slim projection ─────────


class TestDedupeDfDefaultBackendNe:
    def _df(self):
        return pl.DataFrame({
            "first_name": ["Homer", "Homer"],
            "last_name": ["Simpson", "Simpson"],
            "city": ["Springfield", "Springfield"],
            "phone": ["5551111", "5559999"],
        })

    def _blocking(self):
        return BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["city"])],
            max_block_size=1000, skip_oversized=False,
        )

    def _fields(self):
        return [
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=2, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="exact", levels=2),
        ]

    def test_homonym_pair_merges_without_ne_and_separates_with_ne(self):
        # Native ENABLED: see TestBucketSlimProjectionKeepsNeField above --
        # `_fs_native_eligible` declines NE-bearing matchkeys (N4), so this
        # runs the real decline rather than an env override.
        from goldenmatch import dedupe_df

        config_a = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="fs", type="probabilistic", fields=self._fields(), link_threshold=0.5,
            )],
            blocking=self._blocking(),
        )
        result_a = dedupe_df(self._df(), config=config_a)
        assert len(result_a.clusters) == 1  # homonyms merge -- the failure NE exists to fix

        config_b = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name="fs", type="probabilistic", fields=self._fields(),
                negative_evidence=[
                    NegativeEvidenceField(field="phone", scorer="exact", threshold=1.0, penalty_bits=20.0),
                ],
                link_threshold=0.5,
            )],
            blocking=self._blocking(),
        )
        result_b = dedupe_df(self._df(), config=config_b)
        assert len(result_b.clusters) == 2 or result_b.clusters == {}  # NE separates them
