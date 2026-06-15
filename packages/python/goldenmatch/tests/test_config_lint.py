"""Contract for the pre-flight config linter + its registry-as-source-of-truth.

Locks: registry integrity, that rule thresholds stay tied to the auto-config
source constants, each rule's firing logic, that every finding carries the
canonical doc-anchored reason, and that the generated docs never drift from the
registry (the no-drift guarantee that motivated this design).
"""
from __future__ import annotations

from types import SimpleNamespace

import goldenmatch.core.config_lint.rules as rules_mod
import pytest
from goldenmatch.core import config_lint as cl
from goldenmatch.core.config_lint import docgen
from goldenmatch.core.config_lint.registry import LintInput, slugify

# ── tiny duck-typed config builders (the rules access these attrs) ──────────

def _cfg(*, blocking_keys=(), fuzzy_fields=(), backend=None):
    blocking = SimpleNamespace(
        keys=[SimpleNamespace(fields=list(k)) for k in blocking_keys],
        passes=[],
    )
    mk = SimpleNamespace(
        type="weighted",
        fields=[SimpleNamespace(field=c, column=None) for c in fuzzy_fields],
    )
    return SimpleNamespace(
        blocking=blocking,
        backend=backend,
        get_matchkeys=lambda: [mk] if fuzzy_fields else [],
    )


def _inp(row_count, *, cardinality=None, nulls=None):
    return LintInput(
        row_count=row_count,
        cardinality_ratio=cardinality or {},
        null_rate=nulls or {},
        col_type={},
    )


# ── registry integrity ──────────────────────────────────────────────────────

def test_registry_nonempty_and_anchors_resolve():
    rs = cl.all_rules()
    assert rs, "no lint rules registered"
    ids = [r.id for r in rs]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    for r in rs:
        assert r.doc_anchor == f"config-linter#{slugify(r.title)}"
        assert r.rationale.strip() and r.fires_when.strip()


def test_thresholds_tied_to_source_constants():
    # If the planner constant moves, this fails -- the linter must track it.
    from goldenmatch.core.autoconfig_planner_rules import SIMPLE_PLAN_MAX_PAIRS
    assert rules_mod._SIMPLE_PLAN_MAX_PAIRS == SIMPLE_PLAN_MAX_PAIRS


# ── rule firing logic ───────────────────────────────────────────────────────

def test_near_unique_blocking_fires_and_clears():
    cfg = _cfg(blocking_keys=[["id"]])
    assert any(f.rule_id == "blocking.near_unique"
               for f in cl.lint(cfg, _inp(1000, cardinality={"id": 1.0})))
    assert not any(f.rule_id == "blocking.near_unique"
                   for f in cl.lint(cfg, _inp(1000, cardinality={"id": 0.3})))


def test_pair_explosion_fires_on_near_constant_key():
    # near-constant key on 2M rows -> ~huge intra-block pairs
    cfg = _cfg(blocking_keys=[["state"]])
    fired = cl.lint(cfg, _inp(2_000_000, cardinality={"state": 0.00001}))
    assert any(f.rule_id == "blocking.pair_explosion" for f in fired)
    # a selective key on the same rows does not explode
    ok = cl.lint(cfg, _inp(2_000_000, cardinality={"state": 0.5}))
    assert not any(f.rule_id == "blocking.pair_explosion" for f in ok)


def test_null_heavy_field_fires():
    cfg = _cfg(fuzzy_fields=["name"])
    assert any(f.rule_id == "scoring.null_heavy_field"
               for f in cl.lint(cfg, _inp(1000, nulls={"name": 0.7})))
    assert not any(f.rule_id == "scoring.null_heavy_field"
                   for f in cl.lint(cfg, _inp(1000, nulls={"name": 0.1})))


@pytest.mark.parametrize("backend,expect", [
    (None, True), ("polars-direct", True), ("chunked", False), ("duckdb", False),
])
def test_inmemory_at_scale(backend, expect):
    cfg = _cfg(fuzzy_fields=["name"], backend=backend)
    fired = any(f.rule_id == "scale.inmemory_backend_at_scale"
                for f in cl.lint(cfg, _inp(2_000_000, nulls={"name": 0.0})))
    assert fired is expect


# ── findings carry the canonical, doc-anchored reason ───────────────────────

def test_findings_reason_is_the_registry_rationale():
    cfg = _cfg(blocking_keys=[["id"]])
    findings = cl.lint(cfg, _inp(1000, cardinality={"id": 1.0}))
    assert findings
    by_id = {r.id: r for r in cl.all_rules()}
    for f in findings:
        rule = by_id[f.rule_id]
        # the stated reason IS the documented reason -- no drift, by construction
        assert f.rationale == rule.rationale
        assert f.doc_anchor == rule.doc_anchor
        assert f.provenance == "deterministic"


def test_lint_is_pure_and_fail_open():
    # an empty / weird config must not raise and must return a list
    assert cl.lint(SimpleNamespace(), _inp(0)) == [] or isinstance(cl.lint(SimpleNamespace(), _inp(0)), list)


# ── no-drift: the generated docs match the registry ─────────────────────────

def test_docs_not_stale():
    assert docgen.docs_are_current(), (
        "config-linter.mdx is stale vs the rule registry. "
        "Run: python scripts/gen_lint_docs.py --write"
    )


def test_every_rule_has_a_heading_in_the_docs():
    rendered = docgen.render_lint_docs()
    for r in cl.all_rules():
        assert f"### {r.title}\n" in rendered
        # the heading's slug == the anchor the linter emits
        assert slugify(r.title) == r.doc_anchor.split("#", 1)[1]
