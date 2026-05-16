"""Unit tests for ExecutionPlan.

Spec §Decision space -- the six knobs the planner picks. Frozen
dataclass; defaults match today's polars-direct path so an empty plan
preserves current behavior.
"""
from __future__ import annotations

from goldenmatch.core.execution_plan import ExecutionPlan


def test_execution_plan_defaults_match_current_behavior():
    p = ExecutionPlan()
    assert p.backend == "polars-direct"
    assert p.chunk_size is None
    assert p.max_workers == 4
    assert p.pair_spill_threshold is None
    assert p.clustering_strategy == "in_memory"
    assert p.rule_name is None


def test_execution_plan_is_frozen():
    import dataclasses
    p = ExecutionPlan()
    try:
        p.backend = "duckdb"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ExecutionPlan should be frozen")


def test_execution_plan_construction_with_overrides():
    p = ExecutionPlan(
        backend="chunked",
        chunk_size=250_000,
        max_workers=16,
        pair_spill_threshold="ram",
        clustering_strategy="in_memory",
        rule_name="plan_selected_chunked",
    )
    assert p.backend == "chunked"
    assert p.chunk_size == 250_000


def test_execution_plan_apply_to_config_round_trips():
    """ExecutionPlan.apply_to(config) writes the chosen backend onto
    config.backend; unset knobs leave existing config fields alone."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    mk = MatchkeyConfig(
        name="m",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )
    block = BlockingConfig(keys=[BlockingKeyConfig(fields=["name"])], max_block_size=1000)
    cfg = GoldenMatchConfig(matchkeys=[mk], blocking=block)
    plan = ExecutionPlan(backend="chunked", chunk_size=250_000)
    plan.apply_to(cfg)
    assert cfg.backend == "chunked"
