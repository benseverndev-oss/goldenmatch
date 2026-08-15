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


# ── Identity layers (#2574 Wave 4) ────────────────────────────────────────
#
# The stage emits layers onto InferredSchema so downstream consumers
# (goldenmatch segments) get them without re-detecting.


def _multiparty_ctx(**stage_config) -> PipeContext:
    df = pl.DataFrame({
        "lender_name": ["Acme Bank"],
        "lender_id": ["L1"],
        "borrower_name": ["Jane Roe"],
        "borrower_ssn": ["123456789"],
    })
    return PipeContext(df=df, stage_config=stage_config)


def test_layers_emitted_on_inferred_schema():
    ctx = _multiparty_ctx()
    infer_schema_stage.run(ctx)
    layers = ctx.artifacts["inferred_schema"].layers
    assert len(layers) >= 2
    cols = {c for lyr in layers for c in lyr.columns}
    assert "lender_name" in cols and "borrower_name" in cols


def test_layers_match_a_direct_detector_call():
    """The stage must not transform what the detector returned."""
    import infermap

    ctx = _multiparty_ctx(domain="finance")
    infer_schema_stage.run(ctx)
    emitted = ctx.artifacts["inferred_schema"].layers
    direct = infermap.detect_identity_layers(ctx.df, domain="finance").layers
    assert [(x.role, x.columns, x.score) for x in emitted] == [
        (x.role, x.columns, x.score) for x in direct
    ]


def test_layer_roles_recorded_in_evidence():
    ctx = _multiparty_ctx()
    infer_schema_stage.run(ctx)
    ev = ctx.artifacts["infer_schema_evidence"]
    assert "layer_roles" in ev and "layer_unassigned" in ev
    assert ev["layer_roles"] == [lyr.role for lyr in ctx.artifacts["inferred_schema"].layers]


def test_single_party_frame_yields_one_layer():
    """The common case, not a degenerate one."""
    ctx = _ctx()
    infer_schema_stage.run(ctx)
    assert len(ctx.artifacts["inferred_schema"].layers) == 1


def test_user_supplied_schema_layers_untouched():
    """schema > everything: a pinned schema is passed through, layers and all."""
    user = InferredSchema(domain="user", fields={}, confidence=1.0)
    ctx = _multiparty_ctx(schema=user)
    infer_schema_stage.run(ctx)
    assert ctx.artifacts["inferred_schema"].layers == []


def test_detect_domain_path_is_unperturbed():
    """Guardrail from the design: the pre-existing fields must not move."""
    ctx = _ctx()
    infer_schema_stage.run(ctx)
    inferred = ctx.artifacts["inferred_schema"]
    assert inferred.domain == "finance"
    assert set(inferred.fields) == {"account_number", "currency"}
    assert inferred.confidence > 0.0
