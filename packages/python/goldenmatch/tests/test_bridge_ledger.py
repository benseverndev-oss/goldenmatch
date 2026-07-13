"""Bridge-count tripwire (endgame map, cross-cutting gate).

`_as_polars_df` is the polars re-entry bridge. The endgame's A-series
retires them batch by batch; this ledger pins the EXACT count so no new
bridge lands silently and every A-batch must shrink the number here.
"""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).parent.parent / "goldenmatch"

# A-series ledger: update DOWNWARD only (see
# docs/superpowers/plans/2026-07-13-goldenmatch-arrow-native-endgame.md).
EXPECTED_BRIDGE_CALLS = {
    "core/pipeline.py": 13,  # A1/A2/A4/A7 + A8 (rerank, llm x2, boost) retired
}


def _count_calls(text: str) -> int:
    return len(re.findall(r"_as_polars_df\(", text)) - text.count("def _as_polars_df(")


def test_bridge_call_site_ledger():
    found: dict[str, int] = {}
    for py in PKG.rglob("*.py"):
        n = _count_calls(py.read_text(encoding="utf-8"))
        if n > 0:
            found[py.relative_to(PKG).as_posix()] = n
    assert found == EXPECTED_BRIDGE_CALLS, (
        "Bridge ledger drift. If you ADDED a bridge: don't -- port via the "
        f"seam instead. If you RETIRED one: update the ledger. Found: {found}"
    )
