"""Static candidacy from the codemap import graph.

`docs/agent-codemap.json` records `defines` and `imports` per module across six
packages and is regenerated in CI, so it is the cheapest accurate source for
module-level reachability. It does NOT record symbol-level references, which is
why this phase stops at modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.static import _validate_codemap, unimported_modules  # noqa: E402


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


def test_real_codemap_yields_176_unimported(tmp_path: Path) -> None:
    """Verify the production codemap passes validation and yields exactly 176."""
    cands = unimported_modules()
    assert len(cands) == 176


def test_missing_package_raises(tmp_path: Path) -> None:
    """Missing a required package should raise loudly."""
    truncated = {
        "packages": {
            "goldenmatch": {"modules": [{"module": "goldenmatch.core"}]},
            "goldencheck": {"modules": [{"module": "goldencheck.core"}]},
            # Missing goldenflow, goldenpipe, infermap, goldenanalysis
        }
    }
    with pytest.raises(ValueError, match="Codemap missing required packages"):
        _validate_codemap(truncated)


def test_truncated_below_floor_raises(tmp_path: Path) -> None:
    """Truncated module count should raise loudly."""
    # All packages present but far too few modules (600 < 700)
    truncated = {
        "packages": {
            "goldenmatch": {"modules": [{"module": f"goldenmatch.m{i}"} for i in range(300)]},
            "goldencheck": {"modules": [{"module": f"goldencheck.m{i}"} for i in range(150)]},
            "goldenflow": {"modules": [{"module": f"goldenflow.m{i}"} for i in range(60)]},
            "goldenpipe": {"modules": [{"module": f"goldenpipe.m{i}"} for i in range(50)]},
            "infermap": {"modules": [{"module": f"infermap.m{i}"} for i in range(20)]},
            "goldenanalysis": {"modules": [{"module": f"goldenanalysis.m{i}"} for i in range(20)]},
        }
    }
    with pytest.raises(ValueError, match="Codemap has only .* modules.*floor is"):
        _validate_codemap(truncated)
