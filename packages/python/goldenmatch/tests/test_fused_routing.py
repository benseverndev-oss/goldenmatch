"""Tests for the fused-routing helpers (Stage A: the est-peak-RSS model).

The module under test is pure (no pipeline/controller imports), so these tests
exercise it in isolation.
"""

import pytest
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
    # Pin the constants (monkeypatch.setattr auto-restores after the test) so the
    # arithmetic is exact and independent of the calibrated default of _RSS_SCALE.
    import goldenmatch.core.fused_routing as fr

    monkeypatch.setattr(fr, "_BYTES_PER_PAIR", 64.0)
    monkeypatch.setattr(fr, "_BYTES_PER_CELL", 40.0)
    monkeypatch.setattr(fr, "_BLOCK_CONCURRENCY", 4.0)
    monkeypatch.setattr(fr, "_RSS_SCALE", 1.0)
    # frame_b = 1000 * 2 * 40      = 80_000
    # pairs_b = 10_000 * 64        = 640_000
    # block_b = 100**2 * 8 * 4     = 320_000
    # total   = 1_040_000  -> /1e9 = 0.00104 GB
    est = fr.estimate_classic_match_peak_rss_gb(
        n_rows=1_000, est_pairs=10_000, block_max=100, n_score_cols=2
    )
    assert abs(est - 0.00104) < 1e-9


def test_est_rss_n_score_cols_floor():
    # n_score_cols <= 0 is floored to 1 (a matchkey always materializes >=1 col).
    zero = estimate_classic_match_peak_rss_gb(1_000_000, 0, 0, 0)
    one = estimate_classic_match_peak_rss_gb(1_000_000, 0, 0, 1)
    assert zero == one
    assert zero > 0


# --- Task A.2: calibration against the memcap / scale bench --------------------
#
# The scale bench (`scripts/bench_match_fused_scale.py` via `bench-match-fused.yml`)
# generates a synthetic dedupe frame with keycard=20 (mean block size 20, uniform
# random keys over n/20 distinct keys) and ONE score column ("name"), then measures
# the CLASSIC ("pipeline") path's `peak_rss_mb`. So the FULL-DATA model inputs are:
#   n_score_cols = 1               (only "name" is scored)
#   est_pairs    = 10 * n_rows     (candidate pairs = sum C(k,2); mean block 20 ->
#                                   lambda^2/2 = 200 pairs/block * n/20 blocks = 10n)
#   block_max    = ~43 at 10M      (full-data max of ~n/20 Poisson(20) draws:
#                                   lambda + sqrt(2*lambda*ln(n/20)) ~= 43; the
#                                   block term is negligible here -- 43^2*8*4 ~= 59 KB,
#                                   < 1e-5 of the total -- so its exact value does
#                                   not move the band. This IS the full-data max,
#                                   not a sample value, per spec 4.1.)
#
# CALIB = [(n_rows, est_pairs, block_max, n_score_cols, measured_classic_gb), ...]
#
# ONLY the 10M classic peak is committed/readable: 5.19 GB, cited in
# `goldenmatch/core/fused_match.py`'s module docstring ("2.73 GB vs 5.19 GB at 10M")
# and the bench-match-fused run. `_RSS_SCALE`'s default (0.763) is tuned to land
# this point ~exactly (est 5.19 GB); the physical-size coefficients over-read, so
# the sub-1.0 scale absorbs the residual (spec 4.1: "a single scale coefficient
# absorbs the residual").
CALIB_COMMITTED = [
    # n_rows,      est_pairs,     block_max, n_score_cols, measured_classic_gb
    (10_000_000, 100_000_000, 43, 1, 5.19),
]


@pytest.mark.parametrize(
    "n_rows,est_pairs,block_max,n_cols,measured", CALIB_COMMITTED
)
def test_est_rss_calibrated_to_bench(n_rows, est_pairs, block_max, n_cols, measured):
    est = estimate_classic_match_peak_rss_gb(n_rows, est_pairs, block_max, n_cols)
    assert 0.7 * measured <= est <= 1.3 * measured, f"{est} vs {measured}"


# The 1M and 5M classic peaks are NOT committed in any readable form -- they live
# only in the `bench-match-fused.yml` CI artifact (`peak_rss_mb`, `path=pipeline`).
# The 2026-07-08 bench run recorded the fused/classic RSS *ratios* (1M 1.66x, 5M
# 2.03x, 10M 1.90x) but not the 1M/5M absolute classic peaks -- and an absolute
# cannot be recovered from a ratio alone. Rather than fabricate targets, this is a
# scaffold: fill `measured_classic_gb` for each row from the bench artifact's
# `peak_rss_mb` (path=pipeline) at n=1e6 / 5e6, then drop the skip. The model is
# pairs-dominated and ~linear in n, so the same `_RSS_SCALE` should land these in
# the +/-30% band; if one doesn't, re-confirm the CALIB inputs are full-scale
# (est_pairs = 10*n, block_max the full-data max) before touching the model shape.
CALIB_TODO = [
    # n_rows,     est_pairs,    block_max, n_score_cols, measured_classic_gb (TODO)
    (1_000_000, 10_000_000, 41, 1, None),
    (5_000_000, 50_000_000, 42, 1, None),
]


@pytest.mark.skip(
    reason="TODO: fill 1M/5M measured classic peak_rss (path=pipeline) from the "
    "bench-match-fused.yml artifact; only the 10M=5.19GB point is committed."
)
@pytest.mark.parametrize("n_rows,est_pairs,block_max,n_cols,measured", CALIB_TODO)
def test_est_rss_calibrated_to_bench_1m_5m(
    n_rows, est_pairs, block_max, n_cols, measured
):
    est = estimate_classic_match_peak_rss_gb(n_rows, est_pairs, block_max, n_cols)
    assert 0.7 * measured <= est <= 1.3 * measured, f"{est} vs {measured}"
