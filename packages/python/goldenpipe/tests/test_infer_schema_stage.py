"""Tests for the infer_schema stage."""
from __future__ import annotations

import polars as pl
import pytest

# pandas is an optional/test-only dep across this workspace — skip cleanly
# when it's not installed (per packages/python/CLAUDE.md guidance).
pd = pytest.importorskip("pandas")
from goldencheck_types import FieldMapping, InferredSchema
from goldenpipe.models.context import PipeContext, StageStatus
from goldenpipe.stages.infer_schema import infer_schema_stage


def _ctx(**stage_config) -> PipeContext:
    df = pl.DataFrame({
        "account_number": ["A1234", "A5678"],
        "currency": ["USD", "EUR"],
    })
    return PipeContext(df=df, stage_config=stage_config)


def test_auto_detect_finance():
    ctx = _ctx()
    result = infer_schema_stage.run(ctx)
    assert result.status == StageStatus.SUCCESS
    inferred = ctx.artifacts["inferred_schema"]
    assert inferred is not None
    assert inferred.domain == "finance"


def test_explicit_domain():
    ctx = _ctx(domain="finance")
    infer_schema_stage.run(ctx)
    assert ctx.artifacts["inferred_schema"].domain == "finance"


def test_no_infer_returns_none():
    ctx = _ctx(no_infer=True)
    infer_schema_stage.run(ctx)
    assert ctx.artifacts["inferred_schema"] is None


def test_user_schema_passes_through():
    user = InferredSchema(
        domain="user",
        fields={"x": FieldMapping("x", "ssn", "ssn", 1.0, {})},
        confidence=1.0,
    )
    ctx = _ctx(schema=user)
    infer_schema_stage.run(ctx)
    assert ctx.artifacts["inferred_schema"] is user


def test_conflict_schema_and_domain_raises():
    user = InferredSchema(domain="user", fields={}, confidence=1.0)
    ctx = _ctx(schema=user, domain="finance")
    with pytest.raises(ValueError, match="conflict"):
        infer_schema_stage.run(ctx)


def test_conflict_no_infer_and_domain_raises():
    ctx = _ctx(no_infer=True, domain="finance")
    with pytest.raises(ValueError, match="conflict"):
        infer_schema_stage.run(ctx)


def test_conflict_no_infer_and_schema_raises():
    user = InferredSchema(domain="user", fields={}, confidence=1.0)
    ctx = _ctx(no_infer=True, schema=user)
    with pytest.raises(ValueError, match="conflict"):
        infer_schema_stage.run(ctx)
