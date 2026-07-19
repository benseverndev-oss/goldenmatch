"""Repo-level config-matrix gate tests.

Every suite package's committed config-matrix.mdx must match a fresh render of its
live config surface, and every render must be byte-deterministic (no memory
addresses / set-ordering leaks). Mirrors the per-package `--check` CI runs.
Regenerate a stale page with: python scripts/gen_config_matrix.py --write <pkg>
"""
from __future__ import annotations

import pytest

from config_matrix import REGISTRY
from config_matrix.coverage import coverage
from config_matrix.crossref import stale_env_refs, undocumented_vocab
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


@pytest.mark.parametrize("name", [n for n, s in REGISTRY.items() if s.doc_coverage])
def test_topical_docs_cover_the_canonical_set(name):
    # Every canonical scorer/strategy/transform/... must be documented in its
    # reference page, so a newly-added value is propagated there, not just to the matrix.
    gaps = undocumented_vocab(REGISTRY[name])
    assert not gaps, "undocumented in topical doc: " + "; ".join(f"{g.token} missing from {g.page}" for g in gaps)


@pytest.mark.parametrize("name", [n for n, s in REGISTRY.items() if s.require_full_coverage])
def test_full_explanation_coverage_maintained(name):
    # Packages flagged complete must stay at 100% NL-explanation coverage -- a new
    # field/CLI option/MCP tool without a description regresses this and fails CI.
    cov = coverage(REGISTRY[name])
    total = sum(t for t, _ in cov.values())
    explained = sum(e for _, e in cov.values())
    assert total == explained, (
        f"{name} dropped below full explanation coverage ({explained}/{total}); "
        "add a description to the new knob (Field(description=...) / CLI help / MCP .description)"
    )


@pytest.mark.parametrize("name", list(REGISTRY))
def test_render_is_deterministic(name):
    # Two renders must be byte-identical -- guards against object reprs with
    # memory addresses (goldenflow's TransformInfo) or set-ordering leaking in.
    spec = REGISTRY[name]
    assert render_generated_block(spec) == render_generated_block(spec)
