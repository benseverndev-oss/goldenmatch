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


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _proj_block(df, cols, full_n):
    from goldenmatch.core.blocking_candidates import project_max_block_size
    mb = int(df.group_by(cols).len().get_column("len").max() or 0)
    return project_max_block_size(mb, df.height, full_n)


def test_no_emitted_blocking_pass_exceeds_cap_sparse_zip():
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    # matching_id is the ground-truth record id (`not used for config`), so the
    # real pipeline never feeds it to build_blocking; drop it here too.
    df = make_healthcare_df(30_000, zip_present=0.5).drop("matching_id")  # sparse zip5
    profiles = profile_columns(df)
    full_n = 1_000_000  # simulate scale; v0 path uses full df but we assert projected
    blk = build_blocking(profiles, df, n_rows_full=full_n)
    cap = blk.max_block_size or 5000
    for k in (blk.keys or []):
        assert _proj_block(df, k.fields, full_n) <= cap, f"key {k.fields} oversized"
    for p in (blk.passes or []):
        assert _proj_block(df, p.fields, full_n) <= cap, f"pass {p.fields} oversized"


def test_dense_zip_still_picks_bounded_compound():
    # regression: the good (dense-zip) shape must still get a bounded compound.
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(30_000, zip_present=0.95).drop("matching_id")
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df, n_rows_full=df.height)
    assert blk.keys, "expected a blocking key on the dense-zip shape"


def test_sparse_zip_gets_bounded_compound_not_degenerate():
    """B2: with sparse zip5 (reclassified identifier, ~45% null), the compound
    search must still reach a BOUNDED compound (e.g. zip5+last_name) so blocking
    is non-empty -- not degenerate. zip5 must be usable as a compound component
    despite high null + identifier type."""
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(30_000, zip_present=0.5).drop("matching_id")
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df, n_rows_full=df.height)
    # Non-degenerate: has at least one real blocking key.
    assert blk.keys, "expected a bounded compound key, got degenerate/empty blocking"
    # The bounding column (zip5) should appear in the chosen key/passes.
    all_fields = set()
    for k in (blk.keys or []):
        all_fields.update(k.fields)
    for p in (blk.passes or []):
        all_fields.update(p.fields)
    assert "zip5" in all_fields, f"expected zip5 to bound the compound, got {all_fields}"


def test_max_iterations_scales_with_dataset_size():
    from goldenmatch.core.autoconfig_controller import ControllerBudget
    small = ControllerBudget.for_dataset(10_000).max_iterations
    large = ControllerBudget.for_dataset(2_000_000).max_iterations
    assert large > small, f"expected more iterations at scale, got small={small} large={large}"
    # base/default unchanged for small data
    assert ControllerBudget.for_dataset(10_000).max_iterations == 3
