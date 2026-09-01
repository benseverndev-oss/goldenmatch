"""Module-level static candidacy, from the AST import graph in check_dead_code.py.

This used to read docs/agent-codemap.json directly. check_dead_code.py's own
build_graph_ast() docstring explains why that source is unsound: the codemap
under-records `from <pkg> import <submodule>` edges (verified: `from
goldenmatch.core import sketch` etc. are absent from it). Measured effect: the
codemap-backed version of this module reported 176 unimported modules, 42 of
which the AST graph already knows ARE imported (goldenmatch.core.strsim,
goldenmatch.mcp._ingest, goldenanalysis.mcp.server, goldenmatch.core.refit,
...) -- false candidates purely from the source, before any liveness or
allowlist reasoning runs. Reusing check_dead_code.py's own graph fixes that at
the root instead of re-deriving a second, differently-wrong one.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# static.py sits in scripts/dead_code/, so scripts/ (where check_dead_code.py
# lives) is the parent's parent -- same sys.path pattern report.py already
# uses to reach coverage_paths.
sys.path.insert(0, str(Path(__file__).parent.parent))

from check_dead_code import build_graph_ast  # noqa: E402

# Minimum module count to catch truncation. The real AST graph has ~790 modules
# across 6 packages (goldenmatch:484, goldencheck:116, goldenflow:74,
# goldenpipe:53, infermap:32, goldenanalysis:31). A floor of 700 catches >10%
# loss while allowing normal growth/pruning. Below 700 signals a partial scan.
MIN_MODULES = 700
REQUIRED_PACKAGES = (
    "goldenmatch",
    "goldencheck",
    "goldenflow",
    "goldenpipe",
    "infermap",
    "goldenanalysis",
)


def _build_graphs() -> dict[str, tuple[dict[str, dict], dict[str, set[str]]]]:
    """AST graph for every required package.

    build_graph_ast() raises SystemExit (via check_dead_code._load_pkg's
    sys.exit) for a package missing from docs/agent-codemap.json -- it still
    reads the codemap for each package's source root, even though it ignores
    the codemap's import edges. That must never kill this process, and it
    must never be silently swallowed either: a package that fails to build is
    recorded here and turned into a loud ValueError by _validate_graphs()
    below, naming exactly which package(s) could not be analysed.
    """
    graphs: dict[str, tuple[dict[str, dict], dict[str, set[str]]]] = {}
    failures: dict[str, str] = {}
    for pkg in REQUIRED_PACKAGES:
        try:
            graphs[pkg] = build_graph_ast(pkg)
        except SystemExit as e:
            failures[pkg] = str(e)
    if failures:
        raise ValueError(
            f"AST graph build failed for package(s): {sorted(failures)}. "
            f"Details: {failures}. This most likely means the package is missing "
            "from docs/agent-codemap.json (build_graph_ast still consults it for "
            "the package's source root) or its source tree moved."
        )
    return graphs


def _validate_graphs(module_counts: dict[str, int]) -> None:
    """Raise if the built graph set is truncated or malformed.

    Raises:
        ValueError: if packages are missing or total module count is suspiciously low.
    """
    pkg_names = set(module_counts)
    missing = set(REQUIRED_PACKAGES) - pkg_names
    if missing:
        raise ValueError(
            f"AST graph missing required packages: {sorted(missing)}. "
            f"Found {sorted(pkg_names)}, expected {sorted(REQUIRED_PACKAGES)}. "
            "This may indicate a partial analysis run."
        )

    total_modules = sum(module_counts.values())
    if total_modules < MIN_MODULES:
        raise ValueError(
            f"AST graph has only {total_modules} modules (floor is {MIN_MODULES}). "
            "This may indicate a partial or broken AST scan."
        )


@lru_cache(maxsize=1)
def _graphs() -> dict[str, tuple[dict[str, dict], dict[str, set[str]]]]:
    """Cached: building all six packages' AST graphs is ~seconds of parsing, and
    every public function here (plus liveness.py's registry resolvers) calls it
    independently within the same process -- callers must treat the return value
    as read-only."""
    graphs = _build_graphs()
    _validate_graphs({pkg: len(modules) for pkg, (modules, _) in graphs.items()})
    return graphs


def all_modules() -> set[str]:
    out: set[str] = set()
    for modules, _ in _graphs().values():
        out.update(modules)
    return out


def imported_modules() -> set[str]:
    """Every module named as an import-edge target by any other module."""
    out: set[str] = set()
    for _, graph in _graphs().values():
        for edges in graph.values():
            out.update(edges)
    return out


def unimported_modules() -> set[str]:
    """Modules nothing imports.

    Package roots are excluded: a package __init__ is what other code imports
    BY name, so it never appears in an import list of its own package and would
    be a permanent false positive.
    """
    roots = set(REQUIRED_PACKAGES)
    return {m for m in all_modules() - imported_modules() if m not in roots}
