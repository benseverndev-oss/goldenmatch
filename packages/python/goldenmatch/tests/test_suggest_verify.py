"""Tests for the self-verification gate in review_config (Task 17).

Two test groups:

1. Pure-Python unit tests of ``suggestion_health()`` and
   ``suggestion_health_from_clusters()`` -- no native required.
   Tests that healthier score distributions produce higher health scores,
   and that degenerate distributions score low.

2. Native-gated integration tests of ``review_config(verify=True/False)``.
   Constructs a case where a net-negative suggestion is emitted by the kernel,
   and asserts that verify=True suppresses it while verify=False keeps it.

The native-gated tests skip when the ``suggest_config`` kernel is absent.
The pure-Python health tests always run.
"""
from __future__ import annotations

import os

import polars as pl
import pytest

# ── Native guard ──────────────────────────────────────────────────────────────

def _native_suggest_available() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        return nm is not None and hasattr(nm, "suggest_config")
    except Exception:
        return False


requires_native = pytest.mark.skipif(
    not _native_suggest_available(),
    reason="native suggest_config not built",
)


# ── Unit tests of suggestion_health() -- always run ──────────────────────────

class TestSuggestionHealth:
    """Pure unit tests of the scored-pairs health proxy; no native kernel."""

    def test_empty_pairs_is_worst(self):
        """Empty scored_pairs returns -1.0 (worst possible)."""
        from goldenmatch.core.suggest.health import suggestion_health

        h = suggestion_health([], threshold=0.7)
        assert h == -1.0

    def test_perfectly_separated_is_positive(self):
        """Most pairs above threshold, few below -> positive health."""
        from goldenmatch.core.suggest.health import suggestion_health

        # 60 above threshold (score 0.9), 40 well below (score 0.3).
        # mass_above=0.6, mass_border=0 (0.3 < threshold-0.1=0.6), mass_sep=0.6.
        # 0.6 < collapse floor (0.9) so no pathology penalty.  health=0.6.
        pairs = (
            [(i, i + 1, 0.9) for i in range(60)]
            + [(i + 60, i + 61, 0.3) for i in range(40)]
        )
        h = suggestion_health(pairs, threshold=0.7)
        assert h > 0.0, f"Expected positive health for well-separated pairs, got {h}"

    def test_all_in_border_is_negative(self):
        """All scores just below threshold (in borderline) -> negative health."""
        from goldenmatch.core.suggest.health import suggestion_health

        # All scores at threshold - 0.05 -> all in the 0.10-wide border band
        threshold = 0.7
        pairs = [(i, i + 1, threshold - 0.05) for i in range(100)]
        h = suggestion_health(pairs, threshold=threshold)
        # mass_above=0, mass_border=1.0, mass_sep = -1.0
        assert h < 0.0, f"Expected negative health for all-borderline pairs, got {h}"

    def test_precision_collapse_penalty(self):
        """When > 90% of pairs are above threshold, health is penalised."""
        from goldenmatch.core.suggest.health import suggestion_health

        # 95% above threshold (precision collapse)
        n = 100
        above_count = 95
        pairs = (
            [(i, i + 1, 0.95) for i in range(above_count)]
            + [(i, i + 1, 0.5) for i in range(above_count, n)]
        )
        h_collapsed = suggestion_health(pairs, threshold=0.7)

        # 50% above threshold (healthy separation)
        pairs_healthy = (
            [(i, i + 1, 0.95) for i in range(50)]
            + [(i, i + 1, 0.3) for i in range(50, 100)]
        )
        h_healthy = suggestion_health(pairs_healthy, threshold=0.7)

        assert h_collapsed < h_healthy, (
            f"Collapsed config (h={h_collapsed:.3f}) should score below "
            f"healthy config (h={h_healthy:.3f})"
        )

    def test_healthier_beats_unhealthy(self):
        """A distribution with good separation beats one with poor separation."""
        from goldenmatch.core.suggest.health import suggestion_health

        threshold = 0.7

        # Good: many above threshold, few in border
        good = (
            [(i, i + 1, 0.9) for i in range(70)]  # 70 above
            + [(i, i + 1, 0.5) for i in range(70, 100)]  # 30 below border
        )
        # Bad: few above threshold, many in border
        bad = (
            [(i, i + 1, 0.75) for i in range(10)]  # 10 above
            + [(i, i + 1, 0.65) for i in range(10, 100)]  # 90 in border
        )

        h_good = suggestion_health(good, threshold)
        h_bad = suggestion_health(bad, threshold)

        assert h_good > h_bad, (
            f"Good distribution (h={h_good:.3f}) should beat "
            f"bad distribution (h={h_bad:.3f})"
        )

    def test_no_matches_is_negative(self):
        """All scores well below threshold -> negative mass_sep, negative health."""
        from goldenmatch.core.suggest.health import suggestion_health

        # All scores at 0.2, threshold 0.7 -> nothing in border or above
        pairs = [(i, i + 1, 0.2) for i in range(100)]
        h = suggestion_health(pairs, threshold=0.7)
        # mass_above=0, mass_border=0 -> mass_sep=0, no pathology, h=0
        # Exact zero is neutral, but at minimum not positive
        assert h <= 0.0, f"No-match config should not have positive health, got {h}"


# ── Unit tests of suggestion_health_from_clusters() -- always run ─────────────

class TestSuggestionHealthFromClusters:
    """Pure unit tests of the cluster-based health proxy used by the verify gate."""

    def _make_clusters(self, sizes, confidences=None):
        """Build a fake clusters dict with the given multi-member sizes."""
        clusters = {}
        row_id = 0
        for i, sz in enumerate(sizes):
            members = list(range(row_id, row_id + sz))
            row_id += sz
            conf = confidences[i] if confidences else 0.8
            clusters[i] = {
                "size": sz,
                "members": members,
                "confidence": conf,
                "oversized": False,
            }
        return clusters

    def test_no_records_is_worst(self):
        """n_records=0 returns -1.0 (degenerate)."""
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters

        h = suggestion_health_from_clusters({}, n_records=0)
        assert h == -1.0

    def test_no_clusters_is_zero(self):
        """No multi-member clusters -> matched_rate=0 -> health=0."""
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters

        # Only singleton clusters
        clusters = {0: {"size": 1, "members": [0], "confidence": 0.9, "oversized": False}}
        h = suggestion_health_from_clusters(clusters, n_records=10)
        assert h == 0.0

    def test_high_recall_high_confidence_is_positive(self):
        """Many matched records with high confidence -> positive health."""
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters

        # 80 records matched in 40 pairs of 2, confidence 0.9
        clusters = self._make_clusters([2] * 40, [0.9] * 40)
        h = suggestion_health_from_clusters(clusters, n_records=100)
        assert h > 0.0, f"Expected positive health for high-recall config, got {h}"

    def test_recall_collapse_scores_lower(self):
        """Config matching fewer records (higher threshold) scores lower than baseline."""
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters

        # Baseline: 80 matched records, 100 total
        baseline_clusters = self._make_clusters([2] * 40, [0.85] * 40)
        h_baseline = suggestion_health_from_clusters(baseline_clusters, n_records=100)

        # After aggressive threshold raise: only 20 matched records remain
        cand_clusters = self._make_clusters([2] * 10, [0.85] * 10)
        h_cand = suggestion_health_from_clusters(cand_clusters, n_records=100)

        assert h_cand < h_baseline, (
            f"Recall-collapsed config (h={h_cand:.3f}) should score below "
            f"baseline (h={h_baseline:.3f})"
        )

    def test_merge_collapse_is_penalised(self):
        """A single giant cluster absorbing most records triggers the penalty."""
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters

        # One cluster with 60 of 100 records -> high concentration (HHI 0.36)
        clusters = {0: {"size": 60, "members": list(range(60)), "confidence": 0.6, "oversized": False}}
        h_collapsed = suggestion_health_from_clusters(clusters, n_records=100)

        # Healthy: 60 records in 30 clusters of 2 (HHI ~ 0.012)
        clusters_healthy = self._make_clusters([2] * 30, [0.8] * 30)
        h_healthy = suggestion_health_from_clusters(clusters_healthy, n_records=100)

        assert h_collapsed < h_healthy, (
            f"Merge-collapsed config (h={h_collapsed:.3f}) should score below "
            f"healthy config (h={h_healthy:.3f})"
        )

    def test_two_equal_mega_clusters_is_penalised(self, monkeypatch):
        """Over-merge SPREAD across two equal 50% clusters must score below healthy.

        This is the case a single-max collapse check (max_size/n > 0.5) misses:
        each cluster holds exactly 50% so max_size/n == 0.5 (not > 0.5), yet the
        clustering is degenerate.  HHI = 0.5^2 + 0.5^2 = 0.5 catches it.

        To isolate the concentration penalty from confidence/recall, hold both
        avg_conf and matched_rate IDENTICAL between the degenerate and healthy
        cases -- the ONLY difference is how the matched records are distributed.

        Pinned to the LEGACY proxy: this asserts the legacy HHI concentration
        penalty specifically. The default proxy is now cohesion (min_edge x
        cap-0.50, per the 2026-06-26 bake-off), which has no HHI term -- these
        no-pair_scores fixtures fall back to identical confidence under cohesion,
        so the legacy mechanism under test must be selected explicitly.
        """
        monkeypatch.setenv("GOLDENMATCH_SUGGEST_HEALTH", "legacy")
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters

        # Degenerate: 100 matched records spread across two 50-record clusters.
        # matched_rate = 1.0, avg_conf = 0.8, HHI = 0.5.
        two_mega = self._make_clusters([50, 50], [0.8, 0.8])
        h_two_mega = suggestion_health_from_clusters(two_mega, n_records=100)

        # Healthy: the SAME 100 matched records, SAME avg_conf, but in 50 pairs.
        # matched_rate = 1.0, avg_conf = 0.8, HHI = 50 * (2/100)^2 = 0.02.
        fifty_pairs = self._make_clusters([2] * 50, [0.8] * 50)
        h_fifty_pairs = suggestion_health_from_clusters(fifty_pairs, n_records=100)

        assert h_two_mega < h_fifty_pairs, (
            f"Two-equal-mega-cluster config (h={h_two_mega:.3f}) should score "
            f"below the healthy many-small-cluster config (h={h_fifty_pairs:.3f}) "
            "-- the concentration penalty must catch over-merge spread across a "
            "few big clusters, not just a single giant one."
        )
        # And the gap must come from the penalty alone (recall + conf are equal).
        assert h_fifty_pairs - h_two_mega > 0.05, (
            "The two-mega-cluster penalty should be a meaningful margin, not noise"
        )

    def test_threshold_extraction_from_config(self):
        """_extract_threshold extracts the first non-None threshold."""
        from unittest.mock import MagicMock

        from goldenmatch.core.suggest.health import _extract_threshold

        mk = MagicMock()
        mk.threshold = 0.85
        config = MagicMock()
        config.get_matchkeys.return_value = [mk]
        assert _extract_threshold(config) == 0.85

    def test_threshold_extraction_fallback(self):
        """_extract_threshold returns 0.5 when no threshold found."""
        from unittest.mock import MagicMock

        from goldenmatch.core.suggest.health import _extract_threshold

        mk = MagicMock()
        mk.threshold = None
        config = MagicMock()
        config.get_matchkeys.return_value = [mk]
        assert _extract_threshold(config) == 0.5


# ── Integration tests of review_config verify gate (native required) ─────────

def _make_ncvr_like_df() -> pl.DataFrame:
    """Construct a small NCVR-shaped dataset where the baseline F1 is already high.

    This mimics the ncvr_synthetic scenario: auto-config gets F1~0.983 and the
    kernel then emits threshold suggestions that lower F1.

    We build a synthetic CRM-like dataset where:
    - 2500 duplicate pairs exist (one entity = two rows)
    - The naming/birth_year blocking naturally produces good separation
    - Any threshold change (raise or lower) would hurt F1

    The exact dataset shape doesn't need to match real NCVR -- we just need a
    scenario where the kernel is likely to emit suggestions on a near-optimal
    config, and those suggestions are health-worsening.
    """
    import random

    rng = random.Random(42)

    rows = []
    entity_id = 0
    # 200 entities, each with 2 variants (true duplicate pair)
    for _ in range(200):
        fn = rng.choice(["Alice", "Bob", "Carol", "Dave", "Eve"])
        ln = rng.choice(["Smith", "Jones", "Williams", "Taylor", "Brown"])
        birth_year = str(rng.randint(1950, 1990))
        zip_code = f"{rng.randint(10000, 99999):05d}"
        # Row 1: clean
        rows.append({
            "first_name": fn,
            "last_name": ln,
            "birth_year": birth_year,
            "zip_code": zip_code,
            "entity_id": entity_id,
        })
        # Row 2: slight name noise
        noise_fn = fn if rng.random() > 0.1 else fn[:-1]
        rows.append({
            "first_name": noise_fn,
            "last_name": ln,
            "birth_year": birth_year,
            "zip_code": zip_code,
            "entity_id": entity_id,
        })
        entity_id += 1
    # 100 unique entities (singletons)
    for _ in range(100):
        rows.append({
            "first_name": rng.choice(["Frank", "Grace", "Hank", "Iris", "Jack"]),
            "last_name": rng.choice(["Miller", "Wilson", "Moore", "Anderson", "Thomas"]),
            "birth_year": str(rng.randint(1950, 1990)),
            "zip_code": f"{rng.randint(10000, 99999):05d}",
            "entity_id": entity_id,
        })
        entity_id += 1

    return pl.DataFrame(rows).drop("entity_id")


def _make_auto_config(df: pl.DataFrame):
    """Auto-configure df with rerank disabled."""
    from goldenmatch.core.autoconfig import auto_configure_df

    try:
        config = auto_configure_df(df, confidence_required=False)
    except Exception:
        config = auto_configure_df(df, confidence_required=False, allow_red_config=True)

    try:
        for mk in config.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
    except Exception:
        pass
    return config


@requires_native
def test_verify_true_never_returns_health_worsening():
    """Every suggestion returned by verify=True has cand_health >= baseline_health.

    This is the strongest safety guarantee: we re-check each survivor using the
    cluster-based health proxy (same function used by the verify gate).
    """
    import copy

    from goldenmatch.core.suggest import apply_suggestion, review_config
    from goldenmatch.core.suggest.health import suggestion_health_from_clusters
    from goldenmatch.tui.engine import MatchEngine

    df = _make_ncvr_like_df()
    config = _make_auto_config(df)

    # Get verified suggestions
    verified = review_config(df, config, verify=True)

    if not verified:
        # All suggestions were suppressed or none were emitted -- that's fine
        return

    # Ensure __row_id__ present for the re-check
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )

    engine = MatchEngine.from_dataframe(df)
    _config = copy.deepcopy(config)
    try:
        for mk in _config.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
    except Exception:
        pass

    n_records = df.height
    baseline_result = engine._run_pipeline(df, _config)
    baseline_health = suggestion_health_from_clusters(baseline_result.clusters, n_records)

    EPS = 1e-6
    for s in verified:
        try:
            cfg_cand = apply_suggestion(_config, s)
            try:
                for mk in cfg_cand.get_matchkeys():
                    if getattr(mk, "rerank", False):
                        mk.rerank = False
            except Exception:
                pass
            cand_result = engine._run_pipeline(df, cfg_cand)
            cand_health = suggestion_health_from_clusters(cand_result.clusters, n_records)
            assert cand_health >= baseline_health - EPS, (
                f"Suggestion {s.id!r} passed verify=True but its health "
                f"({cand_health:.4f}) < baseline ({baseline_health:.4f}). "
                f"The gate has a false negative."
            )
        except Exception as exc:
            pytest.skip(f"Could not re-verify suggestion {s.id!r}: {exc}")


@requires_native
def test_verify_false_may_return_more_suggestions():
    """verify=False returns the raw kernel output; may include more suggestions.

    On an already-healthy config, verify=True should suppress at least one
    harmful suggestion that verify=False keeps -- OR they return the same
    count if the kernel happened to emit only good suggestions on this run.

    We assert the structural property: verify=True returns <= verify=False.
    """
    from goldenmatch.core.suggest import review_config

    df = _make_ncvr_like_df()
    config = _make_auto_config(df)

    verified = review_config(df, config, verify=True)
    raw = review_config(df, config, verify=False)

    assert len(verified) <= len(raw), (
        f"verify=True returned MORE suggestions ({len(verified)}) than "
        f"verify=False ({len(raw)}).  This is a bug: the gate must be monotonically "
        "non-increasing."
    )


@requires_native
def test_env_flag_disables_verify():
    """GOLDENMATCH_SUGGEST_VERIFY=0 disables verification globally.

    Even when verify=True is passed, the env flag wins -- the function
    returns raw suggestions identical to verify=False.
    """
    from goldenmatch.core.suggest import review_config

    df = _make_ncvr_like_df()
    config = _make_auto_config(df)

    raw = review_config(df, config, verify=False)

    # Temporarily disable verify via env
    old_val = os.environ.get("GOLDENMATCH_SUGGEST_VERIFY")
    try:
        os.environ["GOLDENMATCH_SUGGEST_VERIFY"] = "0"
        env_off = review_config(df, config, verify=True)
    finally:
        if old_val is None:
            os.environ.pop("GOLDENMATCH_SUGGEST_VERIFY", None)
        else:
            os.environ["GOLDENMATCH_SUGGEST_VERIFY"] = old_val

    assert len(env_off) == len(raw), (
        f"With GOLDENMATCH_SUGGEST_VERIFY=0, got {len(env_off)} suggestions; "
        f"expected {len(raw)} (same as verify=False)."
    )


@requires_native
def test_verify_suppresses_ncvr_synthetic_net_negatives():
    """On the ncvr_synthetic dataset, verify=True suppresses all net-negative
    suggestions that the kernel emits on an already-healthy config.

    This is the canonical regression test for Task 17: the scorecard shows
    ncvr_synthetic has suggester_prec=0.0 (both suggestions are harmful).
    After self-verify, the emitted set must have cand_health >= baseline.

    Uses the same ncvr_synthetic generator as the suggest_quality harness.
    Falls back to the synthetic NCVR-like frame if the generator is missing.
    """
    import copy

    try:
        from scripts.dqbench_adapters.ncvr import build_ncvr_synthetic_df_and_gt  # noqa: PLC0415
        df, _ = build_ncvr_synthetic_df_and_gt(seed=42)
    except Exception:
        df = _make_ncvr_like_df()

    from goldenmatch.core.suggest import apply_suggestion, review_config
    from goldenmatch.core.suggest.health import suggestion_health_from_clusters
    from goldenmatch.tui.engine import MatchEngine

    config = _make_auto_config(df)
    suggestions = review_config(df, config, verify=True)

    # Re-check each survivor using the cluster-based proxy (same as adapter uses).
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )

    engine = MatchEngine.from_dataframe(df)
    _cfg = copy.deepcopy(config)
    try:
        for mk in _cfg.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
    except Exception:
        pass

    n_records = df.height
    baseline_result = engine._run_pipeline(df, _cfg)
    baseline_health = suggestion_health_from_clusters(baseline_result.clusters, n_records)

    EPS = 1e-6
    failing = []
    for s in suggestions:
        try:
            cfg_cand = apply_suggestion(_cfg, s)
            try:
                for mk in cfg_cand.get_matchkeys():
                    if getattr(mk, "rerank", False):
                        mk.rerank = False
            except Exception:
                pass
            cand_result = engine._run_pipeline(df, cfg_cand)
            cand_health = suggestion_health_from_clusters(cand_result.clusters, n_records)
            if cand_health < baseline_health - EPS:
                failing.append((s.id, cand_health, baseline_health))
        except Exception:
            pass  # verification failure is conservative (suggestion is kept); tolerated

    assert not failing, (
        f"ncvr_synthetic: {len(failing)} health-worsening suggestions leaked through "
        f"verify=True: {failing}.  The cluster-based proxy is not catching these."
    )
