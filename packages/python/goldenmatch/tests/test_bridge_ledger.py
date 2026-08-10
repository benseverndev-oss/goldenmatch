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
    "core/pipeline.py": 6,  # A1-A8 + A9-slice-1 retired; rebase onto deep-D2b removed the 2 frames-path sites
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


# ---------------------------------------------------------------------------
# pl.from_arrow ratchet
# ---------------------------------------------------------------------------
#
# `_as_polars_df` is not the only way back into polars -- `pl.from_arrow` is the
# larger, previously UNWATCHED surface. #2462 shipped a silent arrow-lane bug
# while the bridge ledger sat green, because the ledger only counted one idiom.
#
# RATCHET, not exact equality: 71 sites across 24 files would fail on every
# unrelated refactor and get muted within a month. This may only go DOWN, and a
# NEW file entering the list fails the build.
#
# A site that is CORRECT (polars-lane-only code, where converting to polars IS
# the right behaviour) should carry an inline `# polars-lane: <reason>` pragma.
# The counter SKIPS pragma'd lines, so declaring a site is how you remove it
# from this number honestly -- which makes the number track UNDECLARED re-entry
# rather than raw occurrences.
# Task 6 pass 1 (2026-08-10): 71 -> 63. Eight sites across seven files were
# READ and declared correct via `# polars-lane:` (legacy-goldencheck compat,
# the measured polars transform engine, polars-declared consumers, and the
# pinned csv/xlsx output contract), so those files left this dict entirely.
# The remaining 63 are REAL debt -- see the PR for why clustering/cluster are
# not a mechanical swap.
EXPECTED_FROM_ARROW: dict[str, int] = {
    "distributed/clustering.py": 17,
    "core/cluster.py": 10,
    "distributed/scoring.py": 6,
    "backends/fs_out_of_core.py": 5,
    "core/pairs.py": 4,
    "core/golden.py": 3,
    "core/pipeline.py": 3,
    "distributed/golden.py": 3,
    "core/blocker.py": 2,
    "identity/block_index.py": 2,
    "identity/fingerprint_batch.py": 2,
    "_api.py": 1,
    "core/autoconfig.py": 1,
    "core/golden_fused.py": 1,
    "core/ingest.py": 1,
    "distributed/dataset.py": 1,
    "semantic/discovery/keys.py": 1,
}

# core/frame.py is excluded as a FORWARD-LOOKING guard, not to mask anything:
# it currently has ZERO pl.from_arrow calls. It IS the seam, so if the
# PolarsFrame backend ever needs the idiom that is its legitimate home and
# should not read as debt. Re-verify the zero if you touch it.
_SEAM_FILE = "core/frame.py"

_FROM_ARROW = re.compile(r"pl\.from_arrow\(")
_PRAGMA = re.compile(r"#\s*polars-lane:")


def _count_from_arrow(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if _FROM_ARROW.search(line) and not _PRAGMA.search(line)
    )


def _scan_from_arrow() -> dict[str, int]:
    found: dict[str, int] = {}
    for py in PKG.rglob("*.py"):
        rel = py.relative_to(PKG).as_posix()
        if rel == _SEAM_FILE:
            continue
        n = _count_from_arrow(py.read_text(encoding="utf-8"))
        if n > 0:
            found[rel] = n
    return found


def test_from_arrow_no_new_files():
    """A file that did not previously re-enter polars must not start."""
    found = _scan_from_arrow()
    new_files = sorted(set(found) - set(EXPECTED_FROM_ARROW))
    assert not new_files, (
        f"NEW file re-entering polars via pl.from_arrow: {new_files}. "
        "Port via the seam, or mark the site `# polars-lane: <reason>` if it "
        "is polars-lane-only code."
    )


def test_from_arrow_count_does_not_grow():
    """Per-file counts may only go DOWN."""
    found = _scan_from_arrow()
    regressed = {
        f: {"found": found[f], "allowed": EXPECTED_FROM_ARROW[f]}
        for f in found
        if f in EXPECTED_FROM_ARROW and found[f] > EXPECTED_FROM_ARROW[f]
    }
    assert not regressed, f"pl.from_arrow count went UP: {regressed}"


def test_from_arrow_ledger_is_ratcheted_down():
    """Retiring sites is a win -- but the ledger has to record it, or the
    ratchet silently loosens and lets the count creep back up later."""
    found = _scan_from_arrow()
    improved = {
        f: {"found": found.get(f, 0), "stale_expectation": EXPECTED_FROM_ARROW[f]}
        for f in EXPECTED_FROM_ARROW
        if found.get(f, 0) < EXPECTED_FROM_ARROW[f]
    }
    assert not improved, (
        "You retired pl.from_arrow sites -- thank you. Now ratchet "
        f"EXPECTED_FROM_ARROW DOWN to match: {improved}"
    )


def test_seam_file_still_has_no_from_arrow():
    """The core/frame.py exclusion is forward-looking. If the seam grows
    pl.from_arrow calls, that is a deliberate decision someone should make
    explicitly rather than inherit from an exclusion written when it was zero."""
    seam = PKG / _SEAM_FILE
    n = _count_from_arrow(seam.read_text(encoding="utf-8"))
    assert n == 0, (
        f"{_SEAM_FILE} now has {n} pl.from_arrow call(s). That may be correct "
        "for the PolarsFrame backend -- if so, pragma them and update this test."
    )
