"""Module-level static candidacy, from the codemap import graph."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CODEMAP = REPO / "docs" / "agent-codemap.json"

# Minimum module count to catch truncation. Real codemap has 788 modules across
# 6 packages (goldenmatch:482, goldencheck:116, goldenflow:74, goldenpipe:53,
# infermap:32, goldenanalysis:31). A floor of 700 catches >10% loss while
# allowing normal growth/pruning. Below 700 signals partial regeneration.
MIN_MODULES = 700
REQUIRED_PACKAGES = {
    "goldenmatch",
    "goldencheck",
    "goldenflow",
    "goldenpipe",
    "infermap",
    "goldenanalysis",
}


def _validate_codemap(cm: dict) -> None:
    """Raise if codemap is truncated or malformed.

    Raises:
        ValueError: if packages are missing or total module count is suspiciously low.
    """
    packages = cm.get("packages", {})
    pkg_names = set(packages.keys())

    missing = REQUIRED_PACKAGES - pkg_names
    if missing:
        raise ValueError(
            f"Codemap missing required packages: {sorted(missing)}. "
            f"Found {sorted(pkg_names)}, expected {sorted(REQUIRED_PACKAGES)}. "
            "This may indicate docs/agent-codemap.json was not fully regenerated."
        )

    total_modules = sum(len(pkg.get("modules", [])) for pkg in packages.values())
    if total_modules < MIN_MODULES:
        raise ValueError(
            f"Codemap has only {total_modules} modules (floor is {MIN_MODULES}). "
            "This may indicate docs/agent-codemap.json was truncated or partially regenerated."
        )


def _codemap() -> dict:
    cm = json.loads(CODEMAP.read_text(encoding="utf-8"))
    _validate_codemap(cm)
    return cm


def all_modules() -> set[str]:
    out: set[str] = set()
    for pkg in _codemap()["packages"].values():
        for m in pkg["modules"]:
            out.add(m["module"])
    return out


def imported_modules() -> set[str]:
    """Every module named as an import by any other module."""
    out: set[str] = set()
    for pkg in _codemap()["packages"].values():
        for m in pkg["modules"]:
            for imp in m.get("imports", []) or []:
                out.add(imp)
    return out


def unimported_modules() -> set[str]:
    """Modules nothing imports.

    Package roots are excluded: a package __init__ is what other code imports
    BY name, so it never appears in an import list of its own package and would
    be a permanent false positive.
    """
    roots = set(_codemap()["packages"])
    return {m for m in all_modules() - imported_modules() if m not in roots}
