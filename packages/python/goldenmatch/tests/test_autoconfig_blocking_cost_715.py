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
    return project_max_block_size(mb, df.num_rows, full_n)


def _proj_block_nonnull(df, field, full_n):
    """#1207: project a SINGLE-field key's block on the NON-NULL subframe.

    The static blocker (`core/blocker.py` ~363-369) turns a null single-field
    block key into a null/sentinel `__block_key__` and filters it BEFORE blocks
    form, so a high-null single-field id key's null bucket never materializes at
    runtime (only the near-unique non-null values block -> tiny blocks). The
    null-INCLUSIVE projection counts that phantom bucket and is a false negative
    for single-field strong-id keys -- mirror the production gate by measuring on
    the non-null rows. (A COMPOUND uses `concat_str`, where nulls propagate /
    stringified-NaN can survive, so compounds keep the null-inclusive check.)
    """
    import polars as pl
    from goldenmatch.core.blocking_candidates import project_max_block_size
    sub = df.filter(pl.col(field).is_not_null())
    mb = int(sub.group_by(field).len().get_column("len").max() or 0) if sub.num_rows else 0
    # Full-N projection unchanged: scale by full_n / FULL df.num_rows (not the
    # non-null height), so a bounded id (zip) still grows with N.
    return project_max_block_size(mb, df.num_rows, full_n)


def test_no_emitted_blocking_pass_exceeds_cap_sparse_zip():
    from goldenmatch.core.autoconfig import _STRONG_EXACT_TYPES, build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    # matching_id is the ground-truth record id (`not used for config`), so the
    # real pipeline never feeds it to build_blocking; drop it here too.
    df = make_healthcare_df(30_000, zip_present=0.5).drop_columns(["matching_id"])  # sparse zip5
    profiles = profile_columns(df)
    col_type = {p.name: p.col_type for p in profiles}
    full_n = 1_000_000  # simulate scale; v0 path uses full df but we assert projected
    blk = build_blocking(profiles, df, n_rows_full=full_n)
    cap = blk.max_block_size or 5000

    def _assert_bounded(fields, label):
        # #1207: single-field strong-id keys are gated on the NON-NULL block (the
        # static blocker drops null single-field keys before blocks form, so their
        # null bucket never exists at runtime). All other keys -- compounds, name,
        # geo -- keep the null-INCLUSIVE projection (concat_str can carry nulls).
        if len(fields) == 1 and col_type.get(fields[0]) in _STRONG_EXACT_TYPES:
            proj = _proj_block_nonnull(df, fields[0], full_n)
        else:
            proj = _proj_block(df, fields, full_n)
        assert proj <= cap, f"{label} {fields} oversized (projected {proj} > {cap})"

    for k in (blk.keys or []):
        _assert_bounded(k.fields, "key")
    for p in (blk.passes or []):
        _assert_bounded(p.fields, "pass")


def test_sparse_healthcare_emits_strong_id_union_1207():
    """#1207: the canonical null-sparse healthcare shape (npi/email/phone all
    high-null + near-unique, sparse zip) must emit a per-identifier UNION, not a
    single bounded compound. Locks in the #1207 behavior so it can't silently
    revert to the pre-#1207 name-compound fallback."""
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(30_000, zip_present=0.5).drop_columns(["matching_id"])
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df, n_rows_full=1_000_000)
    assert blk.strategy == "multi_pass", f"expected union multi_pass, got {blk.strategy}"
    pass_fieldsets = {tuple(p.fields) for p in (blk.passes or [])}
    id_singletons = {("npi",), ("email",), ("phone_number",)}
    present = pass_fieldsets & id_singletons
    assert len(present) >= 2, (
        f"expected >=2 strong-id singleton passes, got {sorted(pass_fieldsets)}"
    )


def test_dense_zip_still_picks_bounded_compound():
    # regression: the good (dense-zip) shape must still get a bounded compound.
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(30_000, zip_present=0.95).drop_columns(["matching_id"])
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df, n_rows_full=df.num_rows)
    assert blk.keys, "expected a blocking key on the dense-zip shape"


def test_sparse_zip_gets_bounded_compound_not_degenerate():
    """B2: with sparse zip5 (reclassified identifier, ~45% null), the compound
    search must still reach a BOUNDED compound (e.g. zip5+last_name) so blocking
    is non-empty -- not degenerate. zip5 must be usable as a compound component
    despite high null + identifier type."""
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(30_000, zip_present=0.5).drop_columns(["matching_id"])
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df, n_rows_full=df.num_rows)
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


# ── #876: a bounded-cardinality SOLE blocking key must be refused at scale ──
# #876 reported precision drift at >1M from a sole `zip` block key (zip wraps at
# ~100K, so block size grows ~N -> bloated cross-cluster blocks the fuzzy scorer
# false-matches). The #715/#723 scale-safe gate already closes this: a sole
# bounded key's projected block exceeds the pairs-per-row budget at scale, so it
# is refused and compounded. These pin that behavior so it can't regress.

def test_sole_zip_exceeds_pairs_budget_at_scale_876():
    """The mechanism, proven on the real constants: a sole bounded-cardinality
    `zip` key at 100M projects ceil(N / domain_cap) rows per block, whose
    pairs-per-row exceeds the budget -> `_is_scale_safe` refuses it (#876)."""
    from goldenmatch.core.autoconfig import (
        _BLOCKING_DOMAIN_CAP,
        _blocking_pairs_per_row_budget,
        _project_pairs_per_row,
    )

    cap = _BLOCKING_DOMAIN_CAP["zip"]              # 100_000 (5-digit US zip)
    full_n = 100_000_000
    projected_block = -(-full_n // cap)            # ceil(N / cap) = 1_000
    pairs_per_row = _project_pairs_per_row(projected_block)   # (1000-1)//2 = 499
    assert pairs_per_row > _blocking_pairs_per_row_budget(), (
        f"sole-zip at {full_n:,} projects {pairs_per_row} pairs/row; this MUST "
        f"exceed the budget {_blocking_pairs_per_row_budget()} so the scale-safe "
        f"gate refuses it (the #876 explosion). If this fails, the gate regressed."
    )


def test_dense_zip_no_sole_bounded_key_at_scale_876():
    """End-to-end: on a dense-zip shape at 100M, auto-config must NOT emit a SOLE
    bounded-cardinality key (zip5 alone); zip5 only survives inside a compound
    (zip5 + a refining field). Non-degenerate blocking is still produced (#876)."""
    from goldenmatch.core.autoconfig import build_blocking, profile_columns
    from repro_issue_715 import make_healthcare_df

    df = make_healthcare_df(30_000, zip_present=0.95).drop_columns(["matching_id"])  # dense zip5
    profiles = profile_columns(df)
    blk = build_blocking(profiles, df, n_rows_full=100_000_000)  # #876 scale

    assert (blk.keys or blk.passes), "expected non-degenerate blocking at scale"
    for k in (blk.keys or []):
        if "zip5" in k.fields:
            assert len(k.fields) >= 2, f"sole bounded key admitted at 100M: {k.fields}"
    for p in (blk.passes or []):
        if "zip5" in p.fields:
            assert len(p.fields) >= 2, f"sole bounded pass admitted at 100M: {p.fields}"
