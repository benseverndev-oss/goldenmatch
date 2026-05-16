"""Verify BlockingProfile exposes estimated_pair_count + an extrapolate()
helper that scales sample -> full-data row count linearly.

Spec §Signals + §Pipeline integration:
docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md.
"""
from __future__ import annotations

from goldenmatch.core.complexity_profile import BlockingProfile


def _make_profile() -> BlockingProfile:
    return BlockingProfile(
        keys_used=[["name"]],
        n_blocks=10,
        total_comparisons=5_000,
        reduction_ratio=0.95,
        block_sizes_p50=20,
        block_sizes_p95=50,
        block_sizes_p99=100,
        block_sizes_max=200,
        singleton_block_count=2,
        oversized_block_count=0,
    )


def test_blocking_profile_estimated_pair_count_uses_total_comparisons():
    """estimated_pair_count is just total_comparisons exposed under the
    planner-friendly name. Same number, named for the planner."""
    p = _make_profile()
    assert p.estimated_pair_count == 5_000


def test_blocking_profile_extrapolate_scales_pair_count_linearly():
    """Sample -> full extrapolation: pair count scales by the
    (n_rows_full / n_rows_sample) ratio."""
    p = _make_profile()
    extrapolated = p.extrapolate_to(n_rows_sample=2_000, n_rows_full=200_000)
    assert extrapolated.estimated_pair_count == 500_000


def test_blocking_profile_extrapolate_identity_when_full_equals_sample():
    p = _make_profile()
    extrapolated = p.extrapolate_to(n_rows_sample=2_000, n_rows_full=2_000)
    assert extrapolated.estimated_pair_count == 5_000
