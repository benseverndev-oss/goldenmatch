"""Repo-level config-matrix generator (all suite packages).

The render half needs pydantic (it walks the packages' schema trees), but
``registry.py`` is pure stdlib -- and the STDLIB-ONLY doc gates
(``check_docs_staleness``, ``check_docs_sections``, ``check_docs_consistency``)
run on a bare ``setup-python`` runner with no synced workspace. Re-exporting
``render`` eagerly here made ``from config_matrix.registry import REGISTRY``
drag pydantic in transitively, so those gates could not share the roster and
each grew its own hardcoded copy of the package list instead.

The render symbols are therefore resolved LAZILY (PEP 562). ``from config_matrix
import REGISTRY, PackageSpec`` and ``from config_matrix.registry import ...``
stay stdlib-only; ``from config_matrix import write_docs`` (etc.) imports
``render`` on first access exactly as before.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import REGISTRY, PackageSpec

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .render import (
        docs_are_current,
        render_generated_block,
        scan_env_vars,
        write_docs,
    )

_RENDER_EXPORTS = frozenset(
    {"docs_are_current", "render_generated_block", "scan_env_vars", "write_docs"}
)

__all__ = [
    "REGISTRY",
    "PackageSpec",
    "docs_are_current",
    "render_generated_block",
    "scan_env_vars",
    "write_docs",
]


def __getattr__(name: str) -> object:
    if name in _RENDER_EXPORTS:
        from . import render

        return getattr(render, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
