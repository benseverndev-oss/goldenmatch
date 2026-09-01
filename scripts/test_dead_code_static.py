"""Static candidacy from the codemap import graph.

`docs/agent-codemap.json` records `defines` and `imports` per module across six
packages and is regenerated in CI, so it is the cheapest accurate source for
module-level reachability. It does NOT record symbol-level references, which is
why this phase stops at modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.static import unimported_modules  # noqa: E402


def test_a_widely_imported_module_is_not_a_candidate():
    assert "goldenmatch.core.frame" not in unimported_modules()


def test_package_roots_are_never_candidates():
    """A package __init__ is the import target, so it has no importer by
    construction and would otherwise be a permanent false positive."""
    cands = unimported_modules()
    assert "goldenmatch" not in cands
    assert "goldenflow" not in cands


def test_the_candidate_set_is_a_minority_of_modules():
    """If most modules look unimported the graph is being read wrong, and the
    report would drown its reviewer in false positives."""
    cands = unimported_modules()
    assert 0 < len(cands) < 300
