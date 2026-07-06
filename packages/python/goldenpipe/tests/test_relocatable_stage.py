"""Relocatable-stage contract, Phase A: the Frame seam + the location guard.

Design: docs/design/2026-07-06-goldenpipe-relocatable-stage-contract.md.

Phase A is deliberately inert -- it adds the Arrow-capable boundary (Frame /
LocalFrame + ctx.frame) and the stage `location` dispatch, but only the in-process
`local` path runs. These tests prove the contract mechanism works (Frame round-trips
correctly, the in-process path is zero-copy) and that a non-local stage fails loudly
rather than silently running in-process.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import ExecutionPlan, PlannedStage
from goldenpipe.engine.runner import Runner
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.frame import Frame, LocalFrame
from goldenpipe.models.stage import StageInfo, stage


class TestLocalFrame:
    def test_polars_is_zero_copy_identity(self):
        # The whole point of Phase A: the in-process path returns the backing
        # DataFrame BY REFERENCE, so introducing the seam adds no copy.
        df = pl.DataFrame({"a": [1, 2, 3]})
        assert LocalFrame(df).polars() is df

    def test_satisfies_frame_protocol(self):
        assert isinstance(LocalFrame(pl.DataFrame({"a": [1]})), Frame)

    def test_arrow_round_trip_preserves_data(self):
        df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"], "c": [1.5, 2.5, None]})
        batches = list(LocalFrame(df).arrow_batches())
        assert LocalFrame.from_arrow(batches).polars().equals(df)

    def test_arrow_batches_are_pyarrow_recordbatches(self):
        import pyarrow as pa

        batches = list(LocalFrame(pl.DataFrame({"a": [1, 2]})).arrow_batches())
        assert batches and all(isinstance(b, pa.RecordBatch) for b in batches)


class TestContextFrameSeam:
    def test_frame_getter_is_zero_copy_view(self):
        df = pl.DataFrame({"a": [1, 2]})
        ctx = PipeContext(df=df)
        assert isinstance(ctx.frame, LocalFrame)
        assert ctx.frame.polars() is df  # no copy, no Arrow round-trip

    def test_frame_none_when_df_none(self):
        assert PipeContext().frame is None

    def test_frame_setter_writes_back_to_df(self):
        ctx = PipeContext(df=pl.DataFrame({"a": [1]}))
        ctx.frame = LocalFrame(pl.DataFrame({"a": [9, 9]}))
        assert ctx.df.to_series().to_list() == [9, 9]

    def test_frame_setter_none_clears_df(self):
        ctx = PipeContext(df=pl.DataFrame({"a": [1]}))
        ctx.frame = None
        assert ctx.df is None

    def test_df_field_still_constructs(self):
        # The property must NOT shadow the dataclass field -- existing callers do
        # PipeContext(df=...).
        df = pl.DataFrame({"a": [1]})
        assert PipeContext(df=df).df is df


class TestStageLocation:
    def test_default_is_local(self):
        assert StageInfo(name="x", produces=[], consumes=[]).location == "local"

    def test_decorator_passes_location_through(self):
        @stage(name="remote_x", produces=[], consumes=[], location="remote")
        def remote_x(ctx: PipeContext) -> StageResult:
            return StageResult(status=StageStatus.SUCCESS)

        assert remote_x.info.location == "remote"


@stage(name="local_ok", produces=["df"], consumes=[])
def _local_ok(ctx: PipeContext) -> StageResult:
    return StageResult(status=StageStatus.SUCCESS)


@stage(name="remote_stage", produces=["df"], consumes=[], location="remote")
def _remote_stage(ctx: PipeContext) -> StageResult:  # pragma: no cover - never runs
    return StageResult(status=StageStatus.SUCCESS)


def _plan(*stages) -> ExecutionPlan:
    return ExecutionPlan(stages=[
        PlannedStage(name=s.info.name, stage=s, spec=StageSpec(use=s.info.name))
        for s in stages
    ])


class TestRunnerLocationGuard:
    def test_local_stage_runs(self):
        ctx = PipeContext()
        result = Runner(registry=StageRegistry()).run(_plan(_local_ok), ctx)
        assert result["local_ok"].status == StageStatus.SUCCESS

    def test_remote_stage_raises_not_implemented(self):
        # A non-local location must fail loudly (Phase C not built), not silently
        # run in-process.
        ctx = PipeContext()
        with pytest.raises(NotImplementedError, match="Phase C"):
            Runner(registry=StageRegistry()).run(_plan(_remote_stage), ctx)
