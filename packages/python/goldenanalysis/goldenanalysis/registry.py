"""Analyzer discovery via the ``goldenanalysis.analyzers`` entry-point group.

Entry-points are the extensibility story (third parties register their own without
editing this package). A hard-coded fallback map mirrors the entry-points table so
discovery is reliable under editable installs, where entry-points can be missing
(see packages/python/CLAUDE.md). Keep the two in sync.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points

from goldenanalysis.analyzers.base import Analyzer

_GROUP = "goldenanalysis.analyzers"

# Fallback: name -> (module, class). Mirror pyproject's [project.entry-points].
_FALLBACK: dict[str, tuple[str, str]] = {
    "frame.summary": ("goldenanalysis.analyzers.frame_summary", "FrameSummaryAnalyzer"),
}


def _entry_point_names() -> set[str]:
    try:
        return {ep.name for ep in entry_points(group=_GROUP)}
    except Exception:
        return set()


def _resolve_class(name: str):
    # Entry-points first (lets external packages register), then the fallback map.
    try:
        for ep in entry_points(group=_GROUP):
            if ep.name == name:
                return ep.load()
    except Exception:
        pass
    target = _FALLBACK.get(name)
    if target is not None:
        module, attr = target
        return getattr(importlib.import_module(module), attr)
    raise KeyError(f"unknown analyzer {name!r}; available: {available_analyzers()}")


def available_analyzers() -> list[str]:
    """Sorted names of every discoverable analyzer (entry-points ∪ fallback)."""
    return sorted(set(_FALLBACK) | _entry_point_names())


def load_analyzer(name: str) -> Analyzer:
    """Instantiate the analyzer registered under ``name``."""
    return _resolve_class(name)()
