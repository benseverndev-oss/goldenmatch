"""Repo-level config-matrix gate tests.

Every suite package's committed config-matrix.mdx must match a fresh render of its
live config surface, and every render must be byte-deterministic (no memory
addresses / set-ordering leaks). Mirrors the per-package `--check` CI runs.
Regenerate a stale page with: python scripts/gen_config_matrix.py --write <pkg>
"""
from __future__ import annotations

import pytest

from config_matrix import REGISTRY
from config_matrix.crossref import stale_env_refs
from config_matrix.render import (
    MARKER_END,
    MARKER_START,
    _doc_path,
    docs_are_current,
    render_generated_block,
)


@pytest.mark.parametrize("name", list(REGISTRY))
def test_config_matrix_current(name):
    spec = REGISTRY[name]
    assert docs_are_current(spec), (
        f"{spec.doc_path} is stale vs the {name} config surface. "
        f"Run: python scripts/gen_config_matrix.py --write {name}"
    )


@pytest.mark.parametrize("name", list(REGISTRY))
def test_markers_present(name):
    text = _doc_path(REGISTRY[name]).read_text(encoding="utf-8")
    assert MARKER_START in text and MARKER_END in text


@pytest.mark.parametrize("name", list(REGISTRY))
def test_no_stale_env_refs_in_docs(name):
    # Every <PREFIX>_* env var named in the package's other docs must be one the
    # code actually reads (the config-matrix registry is the source of truth).
    hits = stale_env_refs(REGISTRY[name])
    assert not hits, "stale env refs: " + "; ".join(f"{h.token} in {h.page}:{h.line_no}" for h in hits)


@pytest.mark.parametrize("name", list(REGISTRY))
def test_render_is_deterministic(name):
    # Two renders must be byte-identical -- guards against object reprs with
    # memory addresses (goldenflow's TransformInfo) or set-ordering leaking in.
    spec = REGISTRY[name]
    assert render_generated_block(spec) == render_generated_block(spec)
