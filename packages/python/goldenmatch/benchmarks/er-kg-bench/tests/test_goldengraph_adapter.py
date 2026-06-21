"""goldengraph engine adapter -- partition parity with goldenmatch dedupe_df.

`goldengraph.resolve` wraps `gm.dedupe_df` on (name, type), so the adapter's
multi-member partition MUST equal a direct `dedupe_df` on the same fields. This
locks that the adapter's position->record-index mapping doesn't drift. Parity
holds regardless of WHAT dedupe_df merges, so the toy-frame merge-outcome
flakiness (the goldenmatch-kg note) does not affect this assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

pytest.importorskip("goldenmatch")
pytest.importorskip("goldengraph")

from erkgbench.adapters import Record  # noqa: E402
from erkgbench.adapters.goldengraph_adapter import GoldenGraphAdapter  # noqa: E402


def _multi(groups: list[list[int]]) -> frozenset:
    """Multi-member groups as a frozenset of frozensets (singletons are the
    trivial complement, so comparing the >1 groups is sufficient)."""
    return frozenset(frozenset(g) for g in groups if len(g) > 1)


def test_goldengraph_partition_parity(monkeypatch):
    # Disable cross-run auto-config memory so two dedupe_df calls in one process
    # can't drift via the on-disk memory db.
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    import goldenmatch as gm
    import polars as pl

    recs = [
        Record(0, "IBM", "org", "tech company"),
        Record(1, "International Business Machines", "org", "tech company"),
        Record(2, "Apple", "org", "tech company"),
        Record(3, "Apple Inc", "org", "tech company"),
        Record(4, "Microsoft", "org", "tech company"),
        Record(5, "Microsoft Corporation", "org", "tech company"),
    ]
    adapter_part = _multi(GoldenGraphAdapter().resolve(recs))

    # Direct dedupe_df on the SAME fields goldengraph.resolve feeds it now:
    # name+type+context (the adapter passes Record.context, and resolve adds the
    # context column whenever any mention carries one).
    df = pl.DataFrame(
        {
            "name": [r.mention for r in recs],
            "type": [r.entity_type for r in recs],
            "context": [r.context for r in recs],
        }
    )
    res = gm.dedupe_df(df)
    gm_part = _multi(
        [[int(x) for x in info["members"]] for info in res.clusters.values()]
    )
    assert adapter_part == gm_part


def test_goldengraph_resolve_is_complete_partition():
    """Every input record appears exactly once across the returned clusters."""
    recs = [Record(i, f"name{i}", "org", "ctx") for i in range(5)]
    part = GoldenGraphAdapter().resolve(recs)
    flat = sorted(i for g in part for i in g)
    assert flat == list(range(5))
