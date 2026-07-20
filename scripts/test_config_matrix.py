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
from config_matrix.crossref import stale_env_refs, undocumented_vocab, vocab_column_gaps
from config_matrix.manifest import MANIFEST_PATH, manifest_is_current, manifest_json
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


def test_goldencheck_check_types_catalog_matches_source():
    # CHECK_TYPES is the canonical check-type catalog; assert it still equals the
    # `check="..."` literals the code actually emits, so it can't drift into a lie.
    import re

    from goldencheck.models.finding import CHECK_TYPES
    from config_matrix.render import ROOT

    src = ROOT / "packages" / "python" / "goldencheck" / "goldencheck"
    scanned: set[str] = set()
    for p in src.rglob("*.py"):
        if "tests" in p.parts:
            continue
        scanned |= set(re.findall(r'check=["\']([a-z_]+)["\']', p.read_text(encoding="utf-8", errors="ignore")))
    assert scanned == set(CHECK_TYPES), (
        f"CHECK_TYPES drifted from source: only-in-source={sorted(scanned - set(CHECK_TYPES))}, "
        f"only-in-catalog={sorted(set(CHECK_TYPES) - scanned)}"
    )


def test_goldenpipe_builtin_stages_match_pyproject():
    # BUILTIN_STAGES is the canonical stage catalog; assert it equals the declared
    # goldenpipe.stages entry points (+ the always-registered `load`), so it can't
    # drift from what a fresh install actually registers.
    import tomllib

    from goldenpipe.engine.registry import BUILTIN_STAGES
    from config_matrix.render import ROOT

    pp = tomllib.loads(
        (ROOT / "packages" / "python" / "goldenpipe" / "pyproject.toml").read_text(encoding="utf-8")
    )
    eps = set(pp["project"]["entry-points"]["goldenpipe.stages"])
    assert set(BUILTIN_STAGES) == eps | {"load"}, (
        f"BUILTIN_STAGES drifted from pyproject entry points: "
        f"only-in-pyproject={sorted((eps | {'load'}) - set(BUILTIN_STAGES))}, "
        f"only-in-catalog={sorted(set(BUILTIN_STAGES) - (eps | {'load'}))}"
    )


@pytest.mark.parametrize("name", [n for n, s in REGISTRY.items() if s.doc_coverage])
def test_topical_docs_cover_the_canonical_set(name):
    # Every canonical scorer/strategy/transform/... must be documented in its
    # reference page, so a newly-added value is propagated there, not just to the matrix.
    gaps = undocumented_vocab(REGISTRY[name])
    assert not gaps, "undocumented in topical doc: " + "; ".join(f"{g.token} missing from {g.page}" for g in gaps)


@pytest.mark.parametrize("name", list(REGISTRY))
def test_vocab_decision_columns_are_complete(name):
    # A decision vocab that gives some values a `best_for` / `range` hint must give
    # it to EVERY value -- a half-filled column renders blank cells that read to a
    # human as "no guidance". So a newly-added scorer/strategy/backend can't ship
    # without its decision hint.
    gaps = vocab_column_gaps(REGISTRY[name])
    assert not gaps, "vocab column gaps: " + "; ".join(f"{g.token} ({g.page})" for g in gaps)


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


# package -> (recipes page, "module:load_config", minimum config blocks). Each
# config-driven package's recipes page is validated through the SAME load_config a
# real file takes, so a renamed/removed field can't rot the copy-paste recipes.
_RECIPE_PAGES = {
    "goldenmatch": ("docs-site/goldenmatch/recipes.mdx", "goldenmatch.config.loader:load_config", 6),
    "goldencheck": ("docs-site/goldencheck/recipes.mdx", "goldencheck.config.loader:load_config", 3),
    "goldenflow": ("docs-site/goldenflow/recipes.mdx", "goldenflow.config.loader:load_config", 3),
    "goldenpipe": ("docs-site/goldenpipe/recipes.mdx", "goldenpipe.config.loader:load_config", 3),
}


@pytest.mark.parametrize("name", list(_RECIPE_PAGES))
def test_recipe_configs_validate(name):
    # Every ```yaml block on a package's recipes page must be a COMPLETE, valid
    # config validated through its real load_config. A renamed/removed field or
    # vocab value fails schema validation, so the recipes can't reference a dead knob.
    import importlib
    import os
    import re
    import tempfile
    from pathlib import Path

    from config_matrix.render import ROOT

    rel, target, minimum = _RECIPE_PAGES[name]
    mod, fn = target.split(":")
    load_config = getattr(importlib.import_module(mod), fn)

    blocks = re.findall(r"```yaml\n(.*?)```", (ROOT / rel).read_text(encoding="utf-8"), re.DOTALL)
    assert len(blocks) >= minimum, f"expected >={minimum} config blocks on {rel}, found {len(blocks)}"
    errors = []
    for i, block in enumerate(blocks, 1):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        try:
            tmp.write(block)
            tmp.close()
            load_config(Path(tmp.name))  # Path works for every package's loader
        except Exception as exc:  # noqa: BLE001 -- surface which recipe is broken
            errors.append(f"{rel} block #{i}: {str(exc)[:200]}")
        finally:
            os.unlink(tmp.name)
    assert not errors, "invalid recipe config(s):\n" + "\n".join(errors)


@pytest.mark.parametrize("name", list(REGISTRY))
def test_render_is_deterministic(name):
    # Two renders must be byte-identical -- guards against object reprs with
    # memory addresses (goldenflow's TransformInfo) or set-ordering leaking in.
    spec = REGISTRY[name]
    assert render_generated_block(spec) == render_generated_block(spec)


def test_agent_manifest_current():
    # The agent-navigation JSON (docs/agent-manifest.json) is a structured view of
    # the SAME live surface these docs render from. It must match a fresh build, so
    # a new config knob / CLI option / MCP tool / vocab value / env var can't ship
    # without regenerating it -- keeping the "don't grep, look it up" store honest.
    assert manifest_is_current(), (
        f"{MANIFEST_PATH} is stale vs the live config surface. "
        "Run: python scripts/gen_config_matrix.py --manifest"
    )


def test_agent_manifest_is_deterministic():
    # Byte-stable across builds (same discipline as the MDX gate): no set-ordering
    # or object-repr leaks, so the committed JSON doesn't flap between a Windows dev
    # box and the Linux CI runner.
    assert manifest_json() == manifest_json()
