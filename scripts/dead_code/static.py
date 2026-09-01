"""Module-level static candidacy, from the codemap import graph."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CODEMAP = REPO / "docs" / "agent-codemap.json"


def _codemap() -> dict:
    return json.loads(CODEMAP.read_text(encoding="utf-8"))


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
