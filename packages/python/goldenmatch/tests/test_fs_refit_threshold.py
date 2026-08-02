"""FS threshold-refit loop — the bimodality-gated valley objective (Phase 3a).

Design: docs/superpowers/specs/2026-08-02-fs-refit-loop-design.md.

The non-iterated FS path commits a fixed 0.50 link cutoff; on over-merge-prone
shapes (household hard-negatives) the F1-optimal cutoff sits above 0.50.
`fs_refit_threshold` picks the cutoff from the ACTUAL scored-pair distribution by
locating the class-separating VALLEY (density trough with mass on both sides) --
NOT Otsu's variance split, which the mode imbalance of FS scores fools into
cutting inside the true-match mass. The bimodality gate makes it a no-op on
overlapping unimodal-with-tail distributions (historical_50k), so it can't
regress a 0.50-optimal dataset.

These tests lock the pure objective at both shapes it must separate + the guard
rails; the end-to-end recovery / no-regression is covered by the panel gate.
"""
from __future__ import annotations

import numpy as np
from goldenmatch.core.probabilistic import (
    _REFIT_MIN_PAIRS,
    _score_distribution_valley,
    fs_refit_link_threshold,
    fs_refit_threshold,
)


def _arr_from_hist(hist, target=6000):
    """Materialize a score array whose 20-bin histogram matches `hist` in SHAPE.
    Scales counts down toward `target` total but keeps every NONZERO bin >= 1, so
    a small-but-present false band (household bins 13-14) is not lost -- otherwise
    the gap widens and the valley scan's cut shifts (the reason the raw down-scale
    was lossy)."""
    nb = len(hist)
    div = max(1.0, sum(hist) / target)
    out = []
    for i, c in enumerate(hist):
        if c <= 0:
            continue
        n = max(1, round(c / div))
        out.extend([(i + 0.5) / nb] * n)
    return np.array(out, dtype=np.float64)


# Measured in-pipeline shapes (20-bin histograms of the scored-pair scores):
# household: a false-pair band (bins 12-14), a GAP (bin 15 = 0), then the huge
# true-match mass (bins 16-19). The valley is at bin 15 -> cut ~0.75.
_HOUSEHOLD = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 146, 8, 12, 0, 260, 1969, 2919, 1056]
# historical_50k: one non-match mass declining into a shoulder -- no gap.
_HISTORICAL = [0, 0, 0, 0, 0, 0, 549733, 75317, 106218, 154707, 23566, 42769,
               33175, 18622, 17724, 27396, 7119, 13365, 14999, 11406]


class TestValleyDetector:
    def test_household_has_valley_below_the_dominant_mode(self):
        # The true-match mass (bins 16-19) dwarfs the false band (12-14); the
        # valley (bin 15) sits BELOW the global peak, so a peak-anchored detector
        # would miss it. The scan must still find it.
        v = _score_distribution_valley(np.array(_HOUSEHOLD, dtype=np.float64))
        assert v is not None
        assert v == 0.75  # low edge of bin 15 (the gap)

    def test_historical_has_no_valley(self):
        # Monotone decline into a shoulder -- no deep trough with mass on both
        # sides -> not bimodal -> None (the no-regression guard).
        assert _score_distribution_valley(np.array(_HISTORICAL, dtype=np.float64)) is None

    def test_unimodal_is_none(self):
        hist = np.zeros(20)
        hist[16:20] = [100, 400, 500, 200]  # a single high mode, no lower band
        assert _score_distribution_valley(hist) is None

    def test_empty_is_none(self):
        assert _score_distribution_valley(np.zeros(20)) is None


class TestFsRefitThreshold:
    def test_household_moves_to_the_valley(self):
        arr = _arr_from_hist(_HOUSEHOLD)
        t = fs_refit_threshold(arr, default_link=0.50)
        assert t == 0.75  # cut at the valley, recovering the over-merge

    def test_historical_keeps_default(self):
        arr = _arr_from_hist(_HISTORICAL)
        assert fs_refit_threshold(arr, default_link=0.50) == 0.50

    def test_too_few_pairs_keeps_default(self):
        assert fs_refit_threshold(np.array([0.9, 0.8, 0.7]), default_link=0.50) == 0.50
        assert _REFIT_MIN_PAIRS > 3

    def test_unimodal_high_keeps_default(self):
        rng = np.random.RandomState(0)
        arr = rng.uniform(0.8, 1.0, 5000)  # one clean mode, no false band
        assert fs_refit_threshold(arr, default_link=0.50) == 0.50

    def test_valley_result_is_clamped(self):
        # A valley far in the tail is clamped to the sane band [0.40, 0.90].
        hist = np.zeros(20)
        hist[0:3] = [300, 40, 300]   # valley at bin 1 -> 0.05, clamps up to 0.40
        arr = _arr_from_hist(list(hist))
        t = fs_refit_threshold(arr, default_link=0.50)
        assert 0.40 <= t <= 0.90

    def test_shallow_dip_is_rejected(self):
        # ncvr regime: a shallow trough (~22% of the flank mode) INSIDE the
        # corruption-spread match distribution is a shoulder, not a class boundary.
        # The deep-valley gate (< 10%) must NOT fire (cutting there loses recall).
        hist = [0, 0, 0, 0, 0, 0, 0, 43, 36, 53, 100, 211, 329, 177, 72, 248, 590,
                1473, 1140, 0]  # dip bin14=72 vs flank 329 -> ratio 0.22 > 0.10
        assert _score_distribution_valley(np.array(hist, dtype=np.float64)) is None
        assert fs_refit_threshold(_arr_from_hist(hist), default_link=0.50) == 0.50


class TestClusterShapeGuard:
    """fs_refit_link_threshold accepts the valley candidate ONLY when re-clustering
    there reduces over-merge (max cluster size). This distinguishes a real
    over-merge band (cutting shrinks a giant cluster) from a gap between
    low-scoring true matches and the rest (cutting removes real links)."""

    def test_accepts_when_over_merge_reduced(self):
        # A giant over-merged cluster held by LOW-score edges (0.60) + many tight
        # high-score true pairs (0.95), with a clean gap between. Cutting at the
        # valley shatters the giant cluster (max size 15 -> 2) -> accept. (>=200
        # pairs so the refit engages.)
        pairs = []
        for i in range(15):                  # 15-node clique = 105 weak 0.55 edges
            for j in range(i + 1, 15):
                pairs.append((i, j, 0.55))
        for k in range(150):                 # 150 tight true 2-node pairs at 0.92
            a = 1000 + 2 * k
            pairs.append((a, a + 1, 0.92))
        ia = [p[0] for p in pairs]; ib = [p[1] for p in pairs]; sc = [p[2] for p in pairs]
        t = fs_refit_link_threshold(ia, ib, sc, default_link=0.50)
        assert t > 0.50  # valley between the 0.60 band and 0.95 mass, over-merge reduced

    def test_rejects_when_no_over_merge_reduction(self):
        # All clusters already tight 2-node entities across two score bands; a gap
        # exists but cutting only DROPS real matches (max size stays 2) -> reject.
        pairs = []
        for k in range(150):
            a = 2 * k
            pairs.append((a, a + 1, 0.62))   # true matches at 0.62 (low band)
        for k in range(150):
            a = 2000 + 2 * k
            pairs.append((a, a + 1, 0.95))   # true matches at 0.95
        ia = [p[0] for p in pairs]; ib = [p[1] for p in pairs]; sc = [p[2] for p in pairs]
        assert fs_refit_link_threshold(ia, ib, sc, default_link=0.50) == 0.50


# ── Route-uniformity: the shared pipeline helper all FS routes call ────────────


class TestMaybeRefitAcrossRoutes:
    """Phase 3a route-extension: every FS scoring route (B2c columnar, arrow-
    stream, list/batched, out-of-core, external-blocks) resolves its link cutoff
    through the ONE ``_maybe_refit_link_threshold`` helper, so the refit fires
    uniformly regardless of route. These lock the helper's route-agnostic contract
    for BOTH pair representations (list of tuples + pa.Table) and the guards."""

    @staticmethod
    def _over_merge_pairs():
        # 15-node weak clique (0.55) + 150 tight true pairs (0.92): a valley
        # between the bands that cutting shatters -> refit raises the cutoff.
        pairs = []
        for i in range(15):
            for j in range(i + 1, 15):
                pairs.append((i, j, 0.55))
        for k in range(150):
            a = 1000 + 2 * k
            pairs.append((a, a + 1, 0.92))
        return pairs

    class _MK:  # minimal matchkey stand-in: no explicit user threshold
        link_threshold = None

    class _MKExplicit:
        link_threshold = 0.5

    def _table(self, pairs):
        import pyarrow as pa
        return pa.table({
            "id_a": pa.array([p[0] for p in pairs], pa.int64()),
            "id_b": pa.array([p[1] for p in pairs], pa.int64()),
            "score": pa.array([p[2] for p in pairs], pa.float64()),
        })

    def test_list_and_table_agree(self, monkeypatch):
        # The list-route and arrow-route representations of the SAME pairs must
        # resolve to the SAME refit cutoff -- that's what "uniform across routes"
        # means. Both raise it above the default on the over-merge shape.
        from goldenmatch.core.pipeline import _maybe_refit_link_threshold
        monkeypatch.setenv("GOLDENMATCH_FS_REFIT_THRESHOLD", "1")
        pairs = self._over_merge_pairs()
        t_list = _maybe_refit_link_threshold(self._MK(), 0.50, pairs=pairs)
        t_tbl = _maybe_refit_link_threshold(self._MK(), 0.50, table=self._table(pairs))
        assert t_list == t_tbl
        assert t_list > 0.50

    def test_flag_off_is_noop_both_forms(self, monkeypatch):
        from goldenmatch.core.pipeline import _maybe_refit_link_threshold
        monkeypatch.delenv("GOLDENMATCH_FS_REFIT_THRESHOLD", raising=False)
        pairs = self._over_merge_pairs()
        assert _maybe_refit_link_threshold(self._MK(), 0.50, pairs=pairs) == 0.50
        assert _maybe_refit_link_threshold(self._MK(), 0.50, table=self._table(pairs)) == 0.50

    def test_explicit_user_threshold_is_respected(self, monkeypatch):
        # A caller-set mk.link_threshold must never be overridden by the refit.
        from goldenmatch.core.pipeline import _maybe_refit_link_threshold
        monkeypatch.setenv("GOLDENMATCH_FS_REFIT_THRESHOLD", "1")
        pairs = self._over_merge_pairs()
        assert _maybe_refit_link_threshold(self._MKExplicit(), 0.50, pairs=pairs) == 0.50

    def test_below_min_pairs_is_noop(self, monkeypatch):
        from goldenmatch.core.pipeline import _maybe_refit_link_threshold
        monkeypatch.setenv("GOLDENMATCH_FS_REFIT_THRESHOLD", "1")
        tiny = [(0, 1, 0.9), (2, 3, 0.6)]
        assert len(tiny) < _REFIT_MIN_PAIRS
        assert _maybe_refit_link_threshold(self._MK(), 0.50, pairs=tiny) == 0.50
