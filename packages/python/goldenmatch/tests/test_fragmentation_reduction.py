"""Cross-language parity for the ER-resolution fragmentation reduction.

`_reduce_fragmentation` (cluster membership → resolved/fragmented/undercount) is
the one piece of the resolution tier with NO shared kernel — it's a scalar loop,
not Arrow-bulk muscle, so kernelizing it would pay FFI marshaling on a small
call (against the architecture frame). Instead it's single-sourced across Python
and TS by a shared data-driven fixture (the goldenanalysis quality_rollup /
regressions precedent): both surfaces run their reduction over the SAME synthetic
clusters and must produce identical counts.

Reads the fixture DIRECTLY from the TS parity tree (the single source, also read
by `fragmentation-reduction.parity.test.ts`) — no copy, no second drift surface.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.semantic.key_integrity import _reduce_fragmentation

FIXTURE = (
    Path(__file__).parent.parent
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


def test_matches_fixture() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert cases, "fixture must carry cases"
    for case in cases:
        resolved, fragmented, undercount = _reduce_fragmentation(
            case["member_lists"], case["keyvals"]
        )
        exp = case["expected"]
        assert resolved == exp["resolved_entities"], case["name"]
        assert fragmented == exp["fragmented_entities"], case["name"]
        assert undercount == exp["undercount_estimate"], case["name"]
