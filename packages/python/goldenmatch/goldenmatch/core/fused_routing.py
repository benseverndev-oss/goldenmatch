"""Fused-routing helpers (controller auto-routing to the fused path).

Pure, self-contained routing logic with NO pipeline/controller imports, so it is
independently testable. Stage A ships only the est-peak-RSS model; later stages
add ``config_needs_artifacts`` and ``maybe_route_fused_match`` here.

The est-peak-RSS model estimates the *classic* (non-fused) match path's peak RSS
from signals the controller already holds, so the match-routing post-step can
decide whether the run is under enough memory pressure to route to the fused
kernel (which halves peak RSS but drops artifacts). See the design at
``docs/superpowers/specs/2026-07-09-controller-fused-auto-routing-design.md`` §4.1.

All four coefficients are ``GOLDENMATCH_FUSED_*`` env-overridable so the trigger
tunes the model without a code change; ``_RSS_SCALE`` is the calibration knob
(its default is pinned to the memcap bench's measured classic peak — see
``tests/test_fused_routing.py::test_est_rss_calibrated_to_bench``).
"""

from __future__ import annotations

import os

# Physical-size coefficients (bytes). A scored pair costs ~64 B (two int64 ids +
# an f64 score + list/store overhead); a materialized matchkey cell ~40 B; the
# per-block cdist matrices are float64 (8 B) and BLOCK_CONCURRENCY score in
# parallel at once.
_BYTES_PER_PAIR = float(os.environ.get("GOLDENMATCH_FUSED_BYTES_PER_PAIR", "64"))
_BYTES_PER_CELL = float(os.environ.get("GOLDENMATCH_FUSED_BYTES_PER_CELL", "40"))
_BLOCK_CONCURRENCY = float(os.environ.get("GOLDENMATCH_FUSED_BLOCK_CONCURRENCY", "4"))

# Calibration knob. Default 0.763 pins the model to the ONE committed measured
# classic peak (10M => 5.19 GB, from core/fused_match.py's module docstring /
# the bench-match-fused run). See the calibration test for the derivation of the
# CALIB inputs. Physical-size coefficients over-read the real peak (allocator
# slack, transient frees), so a single sub-1.0 scale absorbs the residual.
_RSS_SCALE = float(os.environ.get("GOLDENMATCH_FUSED_RSS_SCALE", "0.763"))


def estimate_classic_match_peak_rss_gb(
    n_rows: int,
    est_pairs: int,
    block_max: int,
    n_score_cols: int,
) -> float:
    """Estimate the classic match path's peak RSS in GB.

    Args:
        n_rows: full-data row count (n_rows_full).
        est_pairs: extrapolated full-data candidate-pair count
            (``BlockingProfile.estimated_pair_count``).
        block_max: full-data max block size (peak concurrent cdist matrix side).
        n_score_cols: number of matchkey comparison fields materialized.

    The three terms are the materialized matchkey columns, the scored-pairs
    store, and the peak concurrent float64 cdist matrices; a single ``_RSS_SCALE``
    coefficient (calibrated against the memcap bench) absorbs the residual.
    """
    frame_b = n_rows * max(1, n_score_cols) * _BYTES_PER_CELL
    pairs_b = est_pairs * _BYTES_PER_PAIR
    block_b = (block_max**2) * 8 * _BLOCK_CONCURRENCY
    return _RSS_SCALE * (frame_b + pairs_b + block_b) / 1e9
