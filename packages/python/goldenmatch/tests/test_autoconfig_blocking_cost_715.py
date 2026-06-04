"""Tests for project_max_block_size (#715 reopened — full-N block-cost guard)."""

from goldenmatch.core.blocking_candidates import project_max_block_size


def test_project_max_block_size_scales_linearly_with_full_n():
    # A key with max block 250 in a 5K sample projects up toward full N (linear).
    proj = project_max_block_size(sample_max_block=250, sample_n=5_000, full_n=1_000_000)
    assert proj > 250
    # ~linear: 250 * (1_000_000 / 5_000) = 50_000
    assert 40_000 <= proj <= 60_000


def test_project_max_block_size_identity_when_sample_is_full():
    assert project_max_block_size(4055, 200_000, 200_000) == 4055


def test_project_max_block_size_clamped_to_full_n():
    # cannot exceed full_n
    assert project_max_block_size(sample_max_block=900, sample_n=1_000, full_n=10_000) <= 10_000


def test_project_max_block_size_degenerate_inputs():
    assert project_max_block_size(0, 0, 100) == 0
    assert project_max_block_size(10, 100, 0) == 10  # full_n <= sample_n -> identity
