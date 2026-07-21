"""GoldenMatch runs its zero-config dedupe with **polars genuinely uninstalled**.

goldenmatch is arrow-native by design: `polars` is NOT a base dependency (nor an
extra) -- the package ships a lazy `_polars_lazy` proxy and every default path is
meant to run on pyarrow + numpy + rapidfuzz alone. This module imports polars
NOWHERE and proves that contract for the entry point that matters: zero-config
`dedupe_df` on a `pa.Table` (exactly how goldengraph's cross-document entity
resolution calls it).

This is the living guard for the regression that sat red on `main` for ~2 weeks
(fixed in #1956): the bucket fuzzy fallback (`score_buckets._score_block_frame`)
pre-converted an arrow block to polars (`pl.from_arrow`, guarded by an
`isinstance(block_df, pl.DataFrame)` probe that itself forced the polars import),
so every autoconfig iteration crashed with `ModuleNotFoundError: polars` on the
common tiny-N weighted-fuzzy config. Nothing in `ci-required` exercised goldenmatch
with polars absent, so it regressed silently -- this lane closes that gap, the same
way `goldenflow_nopolars` / `goldencheck_nopolars` guard their siblings.

It is `skipif`'d OUT of the normal suite (where polars IS present), so it is inert
there and only executes in the dedicated `goldenmatch_nopolars` CI lane (and any
local run where polars is absent). The native scoring kernel is built for the lane
so the planner takes the `bucket` backend -- the exact path the bug lived on.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# Keep the diagnostics prompt out of the captured output; the RED zero-config
# config on toy data is expected and irrelevant to the polars-free assertion.
os.environ.setdefault("GOLDEN_DIAGNOSTICS", "0")

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    _HAS_POLARS,
    reason="polars-absent proof -- only runs where polars is NOT installed (the goldenmatch_nopolars lane)",
)


def _cluster_of(clusters: dict, row_id: int) -> int:
    """Return the cluster id that ``row_id`` belongs to (raises if unassigned)."""
    for cid, info in clusters.items():
        if row_id in {int(x) for x in info["members"]}:
            return cid
    raise AssertionError(f"row {row_id} not found in any cluster")


def test_import_goldenmatch_without_polars() -> None:
    import goldenmatch  # must not raise, must not import polars

    assert "polars" not in sys.modules
    # the public entry points survive a polars-absent import
    for name in ("dedupe_df", "match_df", "record_fingerprint", "DedupeResult"):
        assert hasattr(goldenmatch, name), name


def test_zero_config_dedupe_df_arrow_is_polars_free() -> None:
    """The bug reproducer: zero-config ``dedupe_df`` on a ``pa.Table`` completes
    with polars absent and clusters the exact duplicates correctly."""
    import goldenmatch as gm
    import pyarrow as pa

    df = pa.table({"name": ["Acme Inc", "Acme Inc", "Beta"], "type": ["org", "org", "org"]})
    result = gm.dedupe_df(df)

    # the two identical "Acme Inc" rows collapse; "Beta" stays separate
    assert _cluster_of(result.clusters, 0) == _cluster_of(result.clusters, 1)
    assert _cluster_of(result.clusters, 2) != _cluster_of(result.clusters, 0)
    # the scoring path must never have reached for polars
    assert "polars" not in sys.modules


def test_zero_config_dedupe_df_larger_block_is_polars_free() -> None:
    """A bigger arrow input (multi-row blocks -> the vectorized scoring lane)
    also completes polars-free, so the guard covers more than the tiny-N path."""
    import goldenmatch as gm
    import pyarrow as pa

    names = [
        "Acme Inc", "Acme Inc", "Acme Incorporated", "Beta LLC", "Beta LLC",
        "Gamma Co", "Gamma Company", "Delta", "Delta", "Epsilon",
    ] * 3
    df = pa.table({"name": names, "type": ["org"] * len(names)})
    result = gm.dedupe_df(df)

    # the two byte-identical "Acme Inc" rows (indices 0 and 1) still co-cluster
    assert _cluster_of(result.clusters, 0) == _cluster_of(result.clusters, 1)
    assert "polars" not in sys.modules
