"""No NEW dead module may appear.

KNOWN_DEAD is a floor to work DOWN, never a bucket to top up -- the same
contract as KNOWN_POLARS_BOUND in scripts/test_cli_polars_free_sweep.py. It is
populated once from the first CI report, emptied by the A1 deletions, and then
this test gates at zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.report import candidates  # noqa: E402

COVERAGE_XML = Path("packages/python/goldenmatch/coverage.xml")

# Populated in Task 7 from the first CI report; emptied by the A1 deletions.
KNOWN_DEAD: set[str] = set()


@pytest.mark.skipif(
    not COVERAGE_XML.exists(),
    reason="needs the combined coverage.xml; runs in the dead_code CI job",
)
def test_no_new_dead_modules():
    found = {c["module"] for c in candidates(COVERAGE_XML)}

    new = found - KNOWN_DEAD
    assert not new, (
        f"NEW dead module(s): {sorted(new)}. Either delete them, or add an "
        f"entry to parity/dead_code.allow explaining why the detector cannot "
        f"see that they are live."
    )

    fixed = KNOWN_DEAD - found
    assert not fixed, (
        f"{sorted(fixed)} are no longer dead -- remove them from KNOWN_DEAD so "
        f"the ratchet keeps its value."
    )
