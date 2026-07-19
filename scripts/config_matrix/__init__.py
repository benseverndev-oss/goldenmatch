"""Repo-level config-matrix generator (all suite packages)."""
from __future__ import annotations

from .registry import REGISTRY, PackageSpec
from .render import docs_are_current, render_generated_block, scan_env_vars, write_docs

__all__ = [
    "REGISTRY",
    "PackageSpec",
    "docs_are_current",
    "render_generated_block",
    "scan_env_vars",
    "write_docs",
]
