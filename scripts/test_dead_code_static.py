"""Static candidacy from the AST import graph in check_dead_code.py.

check_dead_code.py::build_graph_ast does a direct AST scan of the source tree
rather than reading docs/agent-codemap.json, because the codemap under-records
`from <pkg> import <submodule>` edges. It does NOT record symbol-level
references, which is why this phase stops at modules.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from dead_code.static import (  # noqa: E402
    REPO,
    _validate_graphs,
    imported_modules,
    unimported_modules,
)


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


def test_real_ast_graph_yields_134_unimported() -> None:
    """Verify the production AST graph passes validation and yields exactly 134.

    134 (down from the codemap-backed source's 176) is the measured effect of
    switching sources: 42 of the codemap's 176 were false candidates -- modules
    the AST graph already knows are imported via `from <pkg> import
    <submodule>`, which the codemap under-records.
    """
    cands = unimported_modules()
    assert len(cands) == 134


def test_missing_package_raises() -> None:
    """Missing a required package should raise loudly."""
    truncated = {
        "goldenmatch": 300,
        "goldencheck": 150,
        # Missing goldenflow, goldenpipe, infermap, goldenanalysis
    }
    with pytest.raises(ValueError, match="AST graph missing required packages"):
        _validate_graphs(truncated)


def test_truncated_below_floor_raises() -> None:
    """Truncated module count should raise loudly."""
    # All packages present but far too few modules (600 < 700)
    truncated = {
        "goldenmatch": 300,
        "goldencheck": 150,
        "goldenflow": 60,
        "goldenpipe": 50,
        "infermap": 20,
        "goldenanalysis": 20,
    }
    with pytest.raises(ValueError, match="AST graph has only .* modules.*floor is"):
        _validate_graphs(truncated)


def _from_import_submodule_victim() -> str:
    """A real goldenmatch.core submodule imported ONLY via `from goldenmatch.core
    import <name>` somewhere in the source tree -- never via a full dotted
    `import goldenmatch.core.<name>`.

    Chosen dynamically, not hardcoded: pinning one module name would break the
    day it's renamed or deleted, and the test would then be "fixed" by
    swapping in a new hardcoded name rather than actually re-witnessing the
    bug. This is the exact shape of the 42-module bug: `docs/agent-codemap.json`
    under-records this import form, so a codemap-backed static.py would
    wrongly report the victim as unimported.
    """
    core_dir = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "core"
    submodules = {p.stem for p in core_dir.glob("*.py") if p.stem != "__init__"}
    submodules |= {
        p.name for p in core_dir.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
    }

    from_pattern = re.compile(r"from\s+goldenmatch\.core\s+import\s+([^\n]+)")
    dotted_pattern = re.compile(r"\bimport\s+goldenmatch\.core\.(\w+)")

    dotted_imported: set[str] = set()
    from_imported: dict[str, str] = {}  # submodule name -> file it was found in

    for f in (REPO / "packages" / "python").rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for m in dotted_pattern.finditer(text):
            dotted_imported.add(m.group(1))
        for m in from_pattern.finditer(text):
            for name in m.group(1).split(","):
                name = name.strip().split(" as ")[0].strip().strip("()")
                if name in submodules and name not in from_imported:
                    from_imported[name] = str(f)

    only_via_from = sorted(set(from_imported) - dotted_imported)
    if not only_via_from:
        pytest.skip(
            "no goldenmatch.core submodule is imported ONLY via `from "
            "goldenmatch.core import <name>` -- this test can no longer "
            "witness the 42-module bug"
        )
    return f"goldenmatch.core.{only_via_from[0]}"


def test_a_from_import_submodule_is_not_reported_as_unimported():
    """The 42-module bug: `from goldenmatch.core import <submodule>` must count
    as an import edge, not be invisible to the static signal."""
    victim = _from_import_submodule_victim()
    assert victim in imported_modules()
    assert victim not in unimported_modules()
