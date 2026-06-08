"""Term-frequency (TF) adjustment for the Fellegi-Sunter exact-agree level.

TF makes the top-level (exact-agree) weight value-aware: agreeing on a common
shared value ("Smith") weighs less than agreeing on a rare one. It sits ON TOP
of the sigmoid + union-aware EM path and is OFF by default
(`MatchkeyField.tf_adjust=False`).

Parity contract (load-bearing): with TF enabled, the fast path
(`score_probabilistic_fast`) must produce the same scores as the slow path
(`score_probabilistic`). The TF-table key must match the encoding of the
materialized `__xform_<sig>__` column AND the slow path's transformed lookup.
"""
from __future__ import annotations

import math

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.matchkey import precompute_matchkey_transforms
from goldenmatch.core.probabilistic import (
    TF_MIN_U,
    EMResult,
    _tf_adjusted_weight,
    score_probabilistic,
    train_em,
)
from goldenmatch.core.probabilistic_fast import (
    _resolve_probabilistic_fast_path,
    score_probabilistic_fast,
)

# ── 1. Schema ──────────────────────────────────────────────────────────────


class TestTfAdjustSchema:
    def test_default_false(self):
        f = MatchkeyField(field="last_name", scorer="jaro_winkler")
        assert f.tf_adjust is False

    def test_settable_true(self):
        f = MatchkeyField(field="last_name", scorer="jaro_winkler", tf_adjust=True)
        assert f.tf_adjust is True

    def test_round_trips_in_matchkey_config(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="jaro_winkler", tf_adjust=True),
                MatchkeyField(field="first_name", scorer="jaro_winkler"),
            ],
            link_threshold=0.5,
        )
        dumped = mk.model_dump()
        restored = MatchkeyConfig(**dumped)
        assert restored.fields[0].tf_adjust is True
        assert restored.fields[1].tf_adjust is False


# ── 2. EM tables ───────────────────────────────────────────────────────────


def _tf_frame() -> pl.DataFrame:
    # last_name: "smith" common (4x), "rarejones" rare (1x). first_name no TF.
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3, 4],
            "last_name": ["smith", "smith", "smith", "smith", "rarejones"],
            "first_name": ["alice", "bob", "carol", "dave", "eve"],
        }
    )


class TestTrainEmBuildsTfTables:
    def test_tf_tables_only_for_tf_adjust_fields(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2, tf_adjust=True),
                MatchkeyField(field="first_name", scorer="exact", levels=2),
            ],
            link_threshold=0.5,
        )
        df = _tf_frame()
        em = train_em(df, mk, n_sample_pairs=200, max_iterations=5)
        assert em.tf_tables is not None
        assert "last_name" in em.tf_tables
        assert "first_name" not in em.tf_tables

    def test_tf_freqs_sum_to_one(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2, tf_adjust=True),
            ],
            link_threshold=0.5,
        )
        df = _tf_frame()
        em = train_em(df, mk, n_sample_pairs=200, max_iterations=5)
        assert em.tf_tables is not None
        tft = em.tf_tables["last_name"]
        assert math.isclose(sum(tft.values()), 1.0, abs_tol=1e-9)

    def test_common_value_freq_greater_than_rare(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2, tf_adjust=True),
            ],
            link_threshold=0.5,
        )
        df = _tf_frame()
        em = train_em(df, mk, n_sample_pairs=200, max_iterations=5)
        assert em.tf_tables is not None
        tft = em.tf_tables["last_name"]
        assert tft["smith"] > tft["rarejones"]

    def test_tf_table_key_honors_transforms(self):
        """Key form must match apply_transforms(str(value), transforms)."""
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(
                    field="last_name",
                    scorer="exact",
                    levels=2,
                    transforms=["lowercase"],
                    tf_adjust=True,
                ),
            ],
            link_threshold=0.5,
        )
        # Enough rows that EM runs its full path (>= 10 pairs) instead of the
        # too-few-pairs fallback (which doesn't build tf_tables).
        df = pl.DataFrame(
            {
                "__row_id__": list(range(8)),
                "last_name": ["Smith", "SMITH", "smith", "Smith", "Jones", "JONES", "Brown", "brown"],
            }
        )
        em = train_em(df, mk, n_sample_pairs=200, max_iterations=5)
        assert em.tf_tables is not None
        tft = em.tf_tables["last_name"]
        # All rows collapse to lowercase keys.
        assert "smith" in tft
        assert "jones" in tft
        assert "Smith" not in tft
        assert "SMITH" not in tft

    def test_no_tf_tables_when_no_tf_field(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2),
            ],
            link_threshold=0.5,
        )
        df = _tf_frame()
        em = train_em(df, mk, n_sample_pairs=200, max_iterations=5)
        assert em.tf_tables is None


# ── 3. _tf_adjusted_weight ─────────────────────────────────────────────────


class TestTfAdjustedWeight:
    def test_rarer_value_strictly_larger_weight(self):
        m_exact, u_exact, n_distinct = 0.9, 0.1, 100
        w_rare = _tf_adjusted_weight(m_exact, u_exact, 0.001, n_distinct)
        w_common = _tf_adjusted_weight(m_exact, u_exact, 0.2, n_distinct)
        assert w_rare > w_common

    def test_monotonic_decreasing_in_freq(self):
        # Stay strictly below the TF_MAX_U clamp so weights are strictly
        # monotonic. At n_distinct=50 (freq_avg=0.02) and u_exact=0.1, u_v hits
        # the 0.5 ceiling at freq_v=0.1; keep all probes well under that.
        m_exact, u_exact, n_distinct = 0.9, 0.1, 50
        freqs = [0.002, 0.005, 0.01, 0.02, 0.05]
        weights = [_tf_adjusted_weight(m_exact, u_exact, fv, n_distinct) for fv in freqs]
        for a, b in zip(weights, weights[1:]):
            assert a > b

    def test_average_frequency_returns_base_weight(self):
        m_exact, u_exact, n_distinct = 0.9, 0.1, 20
        freq_avg = 1.0 / n_distinct
        w = _tf_adjusted_weight(m_exact, u_exact, freq_avg, n_distinct)
        base = math.log2(m_exact / u_exact)
        assert math.isclose(w, base, abs_tol=1e-9)

    def test_hapax_weight_bounded_by_min_u_clamp(self):
        m_exact, u_exact, n_distinct = 0.9, 0.1, 1_000_000
        # freq_v near zero -> u_v clamped at TF_MIN_U.
        w = _tf_adjusted_weight(m_exact, u_exact, 1e-12, n_distinct)
        ceiling = math.log2(m_exact / TF_MIN_U)
        assert math.isclose(w, ceiling, abs_tol=1e-9)


# ── 4. Slow-path TF moves the score in the right direction ─────────────────


def _em_tf(tf_tables):
    """2-level EM with a strong agree weight on last_name, TF on it."""
    return EMResult(
        m_probs={"last_name": [0.05, 0.95], "first_name": [0.1, 0.9]},
        u_probs={"last_name": [0.90, 0.10], "first_name": [0.95, 0.05]},
        match_weights={"last_name": [-4.17, 3.25], "first_name": [-3.25, 4.17]},
        converged=True,
        iterations=5,
        proportion_matched=0.1,
        tf_tables=tf_tables,
    )


class TestSlowPathTfDirection:
    def test_rare_shared_value_scores_higher(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2, tf_adjust=True),
                MatchkeyField(field="first_name", scorer="exact", levels=2),
            ],
            link_threshold=0.0,  # emit everything, compare scores
        )
        # rows 0,1 share rare "rarejones"; rows 2,3 share common "smith".
        # first_name identical within each pair so first_name contributes equally.
        df = pl.DataFrame(
            {
                "__row_id__": [0, 1, 2, 3],
                "last_name": ["rarejones", "rarejones", "smith", "smith"],
                "first_name": ["xx", "xx", "yy", "yy"],
            }
        )
        # frequency table: smith common, rarejones rare.
        tft = {"last_name": {"rarejones": 0.05, "smith": 0.95}}
        em = _em_tf(tft)
        pairs = score_probabilistic(df, mk, em)
        scores = {(a, b): s for a, b, s in pairs}
        rare_score = scores[(0, 1)]
        common_score = scores[(2, 3)]
        assert rare_score > common_score


# ── 5. tf_adjust=False is byte-identical to today ──────────────────────────


class TestTfDisabledByteIdentical:
    def test_no_tf_field_matches_tf_tables_none(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2),
                MatchkeyField(field="first_name", scorer="exact", levels=2),
            ],
            link_threshold=0.0,
        )
        df = pl.DataFrame(
            {
                "__row_id__": [0, 1, 2, 3],
                "last_name": ["smith", "smith", "jones", "zzz"],
                "first_name": ["a", "a", "b", "c"],
            }
        )
        em_no_tf = EMResult(
            m_probs={"last_name": [0.05, 0.95], "first_name": [0.1, 0.9]},
            u_probs={"last_name": [0.90, 0.10], "first_name": [0.95, 0.05]},
            match_weights={"last_name": [-4.17, 3.25], "first_name": [-3.25, 4.17]},
            converged=True,
            iterations=5,
            proportion_matched=0.1,
            tf_tables=None,
        )
        # An EM with a populated tf_tables but where NO field has tf_adjust=True
        # must score identically (the gate requires both conditions).
        em_with_table = EMResult(
            m_probs={"last_name": [0.05, 0.95], "first_name": [0.1, 0.9]},
            u_probs={"last_name": [0.90, 0.10], "first_name": [0.95, 0.05]},
            match_weights={"last_name": [-4.17, 3.25], "first_name": [-3.25, 4.17]},
            converged=True,
            iterations=5,
            proportion_matched=0.1,
            tf_tables={"last_name": {"smith": 0.5, "jones": 0.5}},
        )
        a = score_probabilistic(df, mk, em_no_tf)
        b = score_probabilistic(df, mk, em_with_table)
        assert a == b

    def test_tf_field_but_tf_tables_none_matches_baseline(self):
        """tf_adjust=True on a field but tf_tables=None -> baseline behavior."""
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(field="last_name", scorer="exact", levels=2, tf_adjust=True),
            ],
            link_threshold=0.0,
        )
        df = pl.DataFrame(
            {"__row_id__": [0, 1, 2], "last_name": ["smith", "smith", "jones"]}
        )
        em = EMResult(
            m_probs={"last_name": [0.05, 0.95]},
            u_probs={"last_name": [0.90, 0.10]},
            match_weights={"last_name": [-4.17, 3.25]},
            converged=True,
            iterations=5,
            proportion_matched=0.1,
            tf_tables=None,
        )
        baseline = score_probabilistic(df, mk, em)
        # flat agree weight 3.25 -> sigmoid(3.25) for the matching pair.
        expected = round(1.0 / (1.0 + 2.0 ** (-3.25)), 4)
        assert (0, 1, expected) in baseline


# ── 6. fast == slow parity WITH TF on (LOAD-BEARING) ───────────────────────


class TestFastSlowParityWithTf:
    def _build(self):
        mk = MatchkeyConfig(
            name="prob",
            type="probabilistic",
            fields=[
                MatchkeyField(
                    field="last_name",
                    scorer="jaro_winkler",
                    levels=2,
                    partial_threshold=0.8,
                    transforms=["lowercase"],
                    tf_adjust=True,
                ),
                MatchkeyField(
                    field="first_name",
                    scorer="jaro_winkler",
                    levels=2,
                    partial_threshold=0.8,
                    transforms=["lowercase"],
                ),
            ],
            link_threshold=0.0,
        )
        df = pl.DataFrame(
            {
                "__row_id__": [0, 1, 2, 3, 4, 5],
                "last_name": ["Smith", "Smith", "Smith", "Rarejones", "Rarejones", "Jones"],
                "first_name": ["Alice", "Alice", "Bob", "Carol", "Carol", "Dave"],
            }
        )
        prepared = precompute_matchkey_transforms(df, [mk])
        em = train_em(prepared, mk, n_sample_pairs=200, max_iterations=8)
        return mk, prepared, em

    def test_em_built_tf_table(self):
        _mk, _prepared, em = self._build()
        assert em.tf_tables is not None
        assert "last_name" in em.tf_tables
        # Keys are lowercased (match xform encoding).
        assert "smith" in em.tf_tables["last_name"]

    def test_fast_equals_slow_with_tf(self):
        mk, prepared, em = self._build()
        spec = _resolve_probabilistic_fast_path(mk, prepared, em)
        assert spec is not None, "fast path must accept a tf_adjust matchkey"
        fast_pairs = score_probabilistic_fast(prepared, spec)
        slow_pairs = score_probabilistic(prepared, mk, em)

        fast_keys = sorted((a, b) for a, b, _ in fast_pairs)
        slow_keys = sorted((a, b) for a, b, _ in slow_pairs)
        assert fast_keys == slow_keys, (
            f"pair-set mismatch:\n  fast={fast_pairs}\n  slow={slow_pairs}"
        )
        for (fa, fb, fs), (sa, sb, ss) in zip(sorted(fast_pairs), sorted(slow_pairs)):
            assert (fa, fb) == (sa, sb)
            assert math.isclose(fs, ss, abs_tol=1e-6), (
                f"score mismatch at ({fa},{fb}): fast={fs} slow={ss}"
            )

    def test_tf_actually_changes_the_score(self):
        """Sanity: the tf-on score for a rare-sharing pair differs from the
        flat (tf-off) score, so the parity test is testing a live code path."""
        mk, prepared, em = self._build()
        # tf-on slow result
        tf_on = {(a, b): s for a, b, s in score_probabilistic(prepared, mk, em)}
        # tf-off: same EM but tf_tables stripped
        em_off = EMResult(
            m_probs=em.m_probs,
            u_probs=em.u_probs,
            match_weights=em.match_weights,
            converged=em.converged,
            iterations=em.iterations,
            proportion_matched=em.proportion_matched,
            tf_tables=None,
        )
        tf_off = {(a, b): s for a, b, s in score_probabilistic(prepared, mk, em_off)}
        # Pair (0,1) shares the COMMON "smith" (freq 0.5 > freq_avg 1/3), so TF
        # must drop its weight relative to the flat (tf-off) score. Confirms the
        # TF code path is live (the parity test would otherwise pass trivially
        # against a no-op).
        assert (0, 1) in tf_on and (0, 1) in tf_off
        assert tf_on[(0, 1)] < tf_off[(0, 1)]
