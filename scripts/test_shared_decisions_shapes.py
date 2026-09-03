"""Tests for the decision-shape classifier used to triage the B0a inventory."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from shared_decisions.shapes import (
    Access,
    access_shapes,
    classify,
    fallback_divergence,
    nullable_fields,
    unguarded_optional,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "scripts" / "fixtures" / "incident_1c843c8a5"
SCHEMAS = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "config" / "schemas.py"


def _classify_source(source: str, field: str) -> list[tuple[str, str]]:
    """Every (shape, detail) for accesses to `field` in a source snippet."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return [
        classify(node, parents.get(node))
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == field
    ]


def test_the_motivating_incident_is_reported_from_the_checked_in_fixture():
    """`1c843c8a5` must be findable without git history staying reachable.

    `scripts/fixtures/incident_1c843c8a5/` holds `blocker.py` and
    `score_buckets.py` verbatim at `1c843c8a5^`. blocker falls back to `[]`;
    score_buckets falls back to `blocking_config.keys`. Two modules, two
    different fallbacks, on `passes` -- the field the incident turned on.
    """
    divergence = fallback_divergence(access_shapes(FIXTURE))

    assert "passes" in divergence, (
        "the incident field is not reported; the classifier cannot find the bug "
        f"that motivated it. reported: {sorted(divergence)}"
    )
    fallbacks = divergence["passes"]
    assert len(fallbacks) > 1
    culprits = {module for group in fallbacks.values() for module in group}
    assert culprits == {"blocker_prefix.py", "score_buckets_prefix.py"}
    assert "blocking_config.keys" in fallbacks
    assert fallbacks["blocking_config.keys"] == {"score_buckets_prefix.py"}


def test_fallback_detail_is_the_operand_fallen_back_to():
    """The detail must be what the reader SUPPLIES -- the disagreeable part.

    Recording the field name, or a bare "FALLBACK", would make every fallback
    look identical and the divergence signal could never fire.
    """
    shapes = _classify_source("x = cfg.passes or cfg.keys\n", "passes")
    assert shapes == [("FALLBACK", "cfg.keys")]

    shapes = _classify_source("x = cfg.passes or []\n", "passes")
    assert shapes == [("FALLBACK", "[]")]


def test_a_final_or_operand_is_truthiness_not_a_fallback():
    """`a or cfg.x` supplies nothing -- there is no operand after it."""
    assert _classify_source("x = other or cfg.passes\n", "passes") == [("TRUTHY", "")]


def test_one_module_with_two_fallbacks_is_not_a_divergence():
    """A single module choosing two fallbacks in two branches is a local choice.

    Reporting it would flood the signal: `core/autoconfig.py` alone falls back
    on `keys` to both `[]` and `[None]` in different helpers, and neither
    disagrees with another module.
    """
    accesses = [
        Access("keys", "core/autoconfig.py", 10, "FALLBACK", "[]"),
        Access("keys", "core/autoconfig.py", 20, "FALLBACK", "[None]"),
    ]
    assert fallback_divergence(accesses) == {}


def test_two_modules_agreeing_on_one_fallback_is_not_a_divergence():
    accesses = [
        Access("keys", "a.py", 10, "FALLBACK", "[]"),
        Access("keys", "b.py", 10, "FALLBACK", "[]"),
    ]
    assert fallback_divergence(accesses) == {}


def test_two_modules_with_different_fallbacks_is_a_divergence():
    accesses = [
        Access("keys", "a.py", 10, "FALLBACK", "[]"),
        Access("keys", "b.py", 10, "FALLBACK", "cfg.passes"),
    ]
    assert fallback_divergence(accesses) == {"keys": {"[]": {"a.py"}, "cfg.passes": {"b.py"}}}


def test_a_write_only_module_is_not_an_unguarded_reader():
    """Setting a field from a CLI flag never has to cope with it being None.

    Without this, `cli/dedupe.py` and `cli/match.py` were reported on `format`
    and `run_name`, where their single access is the write that sets them.
    """
    nullable = {"format": {"OutputConfig"}}
    accesses = [
        Access("format", "core/pipeline.py", 10, "FALLBACK", "'csv'"),
        Access("format", "cli/dedupe.py", 20, "WRITE", ""),
    ]
    assert unguarded_optional(accesses, nullable) == {}

    # ... but the same module READING it bare is reported.
    accesses.append(Access("format", "cli/dedupe.py", 21, "CALL_ARG", "open"))
    assert unguarded_optional(accesses, nullable) == {"format": {"cli/dedupe.py"}}


def test_a_validator_total_field_is_not_reported_unguarded():
    """`default_strategy` is `| None` by annotation and non-None by validator.

    `GoldenRulesConfig._validate_default` raises unless it resolves, so the
    plain readers are correct and reporting them would be noise.
    """
    nullable = {"default_strategy": {"GoldenRulesConfig"}}
    accesses = [
        Access("default_strategy", "core/golden.py", 645, "FALLBACK", "'most_complete'"),
        Access("default_strategy", "core/survivorship/resolve.py", 10, "KEYWORD", ""),
    ]
    assert unguarded_optional(accesses, nullable) == {}


def test_validator_total_claim_still_holds_in_the_schema():
    """Pin the reason `default_strategy` is excluded, not just the exclusion.

    If the validator that makes the field total is ever relaxed, the exclusion
    becomes a suppression of a real finding and this fails.
    """
    pydantic = pytest.importorskip("pydantic")
    import sys

    sys.path.insert(0, str(REPO / "packages" / "python" / "goldenmatch"))
    from goldenmatch.config.schemas import GoldenRulesConfig

    with pytest.raises(pydantic.ValidationError, match="default_strategy"):
        GoldenRulesConfig()


def test_nullable_fields_reads_the_real_schema():
    nullable = nullable_fields(SCHEMAS)
    assert "GoldenMatchConfig" in nullable["golden_rules"]
    assert "BlockingConfig" in nullable["passes"]
    # `keys` is `list[...]` with a default_factory -- never None.
    assert "keys" not in nullable
