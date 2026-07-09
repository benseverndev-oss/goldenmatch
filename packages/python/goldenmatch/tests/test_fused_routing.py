"""Tests for the fused-routing helpers (Stage A: the est-peak-RSS model).

The module under test is pure (no pipeline/controller imports), so these tests
exercise it in isolation.
"""

from goldenmatch.core.fused_routing import estimate_classic_match_peak_rss_gb


def test_est_rss_monotonic_and_components():
    base = estimate_classic_match_peak_rss_gb(
        n_rows=1_000_000, est_pairs=5_000_000, block_max=500, n_score_cols=3
    )
    assert base > 0
    # more pairs -> more RSS; bigger block -> more RSS; more cols -> more RSS
    assert estimate_classic_match_peak_rss_gb(1_000_000, 50_000_000, 500, 3) > base
    assert estimate_classic_match_peak_rss_gb(1_000_000, 5_000_000, 5000, 3) > base
    assert estimate_classic_match_peak_rss_gb(1_000_000, 5_000_000, 500, 10) > base


def test_est_rss_monotonic_in_rows():
    lo = estimate_classic_match_peak_rss_gb(1_000_000, 5_000_000, 500, 3)
    hi = estimate_classic_match_peak_rss_gb(10_000_000, 5_000_000, 500, 3)
    assert hi > lo


def test_est_rss_small_case_hand_computed(monkeypatch):
    # Pin the constants so the arithmetic is exact and independent of any
    # calibrated default of _RSS_SCALE.
    monkeypatch.setenv("GOLDENMATCH_FUSED_BYTES_PER_PAIR", "64")
    monkeypatch.setenv("GOLDENMATCH_FUSED_BYTES_PER_CELL", "40")
    monkeypatch.setenv("GOLDENMATCH_FUSED_BLOCK_CONCURRENCY", "4")
    monkeypatch.setenv("GOLDENMATCH_FUSED_RSS_SCALE", "1.0")
    import importlib

    import goldenmatch.core.fused_routing as fr

    importlib.reload(fr)
    # frame_b = 1000 * 2 * 40      = 80_000
    # pairs_b = 10_000 * 64        = 640_000
    # block_b = 100**2 * 8 * 4     = 320_000
    # total   = 1_040_000  -> /1e9 = 0.00104 GB
    est = fr.estimate_classic_match_peak_rss_gb(
        n_rows=1_000, est_pairs=10_000, block_max=100, n_score_cols=2
    )
    assert abs(est - 0.00104) < 1e-9
    importlib.reload(fr)  # restore module-level constants for other tests


def test_est_rss_n_score_cols_floor():
    # n_score_cols <= 0 is floored to 1 (a matchkey always materializes >=1 col).
    zero = estimate_classic_match_peak_rss_gb(1_000_000, 0, 0, 0)
    one = estimate_classic_match_peak_rss_gb(1_000_000, 0, 0, 1)
    assert zero == one
    assert zero > 0
