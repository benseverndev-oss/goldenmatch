#!/usr/bin/env python3
"""Emit the cross-language parity fixture for the ER-resolution fragmentation
reduction (`goldenmatch.semantic.key_integrity._reduce_fragmentation`).

The reduction (cluster membership -> resolved/fragmented/undercount) has NO
shared kernel -- it's a scalar loop, not Arrow-bulk muscle, so kernelizing it
would pay FFI marshaling on a small call (against the architecture frame).
Instead Python and TS are single-sourced by this data-driven fixture (the
goldenanalysis quality_rollup / regressions parity-fixture precedent): both
surfaces run their reduction over the SAME synthetic clusters and must produce
identical counts.

The `expected` values are computed by the Python reference here, so the fixture
is regenerated in place by `scripts/regen_ts_parity_fixtures.sh` and the
`ts_parity_freshness` gate re-emits + diffs it -- a future change to the
reduction on either surface is caught (TS drift against Python, and Python drift
against the committed fixture). Read directly (no copy) by both
`tests/test_fragmentation_reduction.py` and
`tests/parity/fragmentation-reduction.parity.test.ts`.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.semantic.key_integrity import _reduce_fragmentation

# (name, member_lists indexing into keyvals, keyvals). Synthetic clusters chosen
# to exercise: no resolved entities, a clean multi-member cluster, a single
# fragmented cluster, a mix, all-fragmented, empty, a large same-key cluster, a
# three-way fragment, and unsorted member ids.
_CASES: list[tuple[str, list[list[int]], list[str]]] = [
    ("all_singletons", [[0], [1], [2]], ["a", "b", "c"]),
    ("one_clean_multi", [[0, 1], [2]], ["k", "k", "x"]),
    ("one_fragmented", [[0, 1]], ["k1", "k2"]),
    ("mixed", [[0, 1], [2, 3, 4], [5]], ["a", "a", "b", "b", "c", "d"]),
    ("all_fragmented", [[0, 1], [2, 3]], ["p", "q", "r", "s"]),
    ("empty", [], []),
    ("large_cluster_same_key", [[0, 1, 2, 3]], ["z", "z", "z", "z"]),
    ("three_way_fragment", [[0, 1, 2]], ["a", "b", "c"]),
    ("unsorted_members", [[2, 0], [1, 3]], ["a", "b", "a", "c"]),
]

_OUT = (
    Path(__file__).resolve().parent.parent
    / ".."
    / ".."
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "key-integrity"
    / "fragmentation_reduction_cases.json"
)


def _build() -> dict:
    cases = []
    for name, member_lists, keyvals in _CASES:
        resolved, fragmented, undercount = _reduce_fragmentation(member_lists, keyvals)
        cases.append(
            {
                "name": name,
                "member_lists": member_lists,
                "keyvals": keyvals,
                "expected": {
                    "resolved_entities": resolved,
                    "fragmented_entities": fragmented,
                    "undercount_estimate": undercount,
                },
            }
        )
    return {
        "_comment": (
            "Cross-language parity oracle for the ER-resolution fragmentation "
            "reduction (cluster membership -> resolved/fragmented/undercount). "
            "Generated from the Python reference "
            "goldenmatch.semantic.key_integrity._reduce_fragmentation by "
            "scripts/emit_fragmentation_reduction_fixture.py. Read DIRECTLY by "
            "both the Python test (tests/test_fragmentation_reduction.py) and the "
            "TS test (tests/parity/fragmentation-reduction.parity.test.ts) -- no "
            "copy, no drift surface. member_lists index into keyvals; keyvals "
            "compared by equality only (representation-agnostic)."
        ),
        "cases": cases,
    }


def main() -> None:
    _OUT.write_text(json.dumps(_build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
