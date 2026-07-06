"""Relocatable-stage contract, Phase C: in-engine (remote) stages.

Design + baseline: ``docs/design/2026-07-06-goldenpipe-phasec-baseline-findings.md``.
Phase C turns ``location="remote"`` from "raises NotImplementedError" (Phase A)
into "runs in the engine and keeps the data there". These tests prove the
``DuckDBFrame`` stays engine-resident (lazy) across a chain, is byte-identical to
the local Polars path, and that the Runner routes real ``RemoteStage``s while a
plain remote-marked stage still fails loudly.
"""
from __future__ import annotations

import duckdb
import polars as pl
import pytest
from goldenpipe.adapters.engine import EngineNormalizeStage, RemoteStage
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import ExecutionPlan, PlannedStage
from goldenpipe.engine.runner import Runner
from goldenpipe.models.config import StageSpec
from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.frame import DuckDBFrame, Frame, LocalFrame
from goldenpipe.models.stage import stage

_DF = pl.DataFrame({
    "id": [1, 2, 3],
    "email": ["  A@X.COM  ", "B@Y.COM", " c@z.com "],
    "city": ["NYC", "LA", "Chicago"],
})


def _plan(*stages) -> ExecutionPlan:
    return ExecutionPlan(stages=[
        PlannedStage(name=s.info.name, stage=s, spec=StageSpec(use=s.info.name))
        for s in stages
    ])


class TestDuckDBFrame:
    def test_satisfies_frame_protocol(self):
        rel = duckdb.connect().from_arrow(_DF.to_arrow())
        assert isinstance(DuckDBFrame(rel), Frame)

    def test_polars_materializes_equal(self):
        rel = duckdb.connect().from_arrow(_DF.to_arrow())
        assert DuckDBFrame(rel).polars().sort("id").equals(_DF.sort("id"))

    def test_project_stays_lazy_and_correct(self):
        rel = duckdb.connect().from_arrow(_DF.to_arrow())
        out = DuckDBFrame(rel).project("id, lower(email) AS email, city")
        assert isinstance(out, DuckDBFrame)  # still a lazy engine frame
        assert out.polars().sort("id")["email"].to_list() == [
            "  a@x.com  ", "b@y.com", " c@z.com "]

    def test_arrow_batches_round_trip(self):
        rel = duckdb.connect().from_arrow(_DF.to_arrow())
        got = LocalFrame.from_arrow(DuckDBFrame(rel).arrow_batches()).polars()
        assert got.sort("id").equals(_DF.sort("id"))


class TestEngineNormalizeStage:
    def test_is_remote_capable(self):
        st = EngineNormalizeStage(column="email")
        assert isinstance(st, RemoteStage)
        assert st.remote_capable is True
        assert st.info.location == "remote"

    def test_result_is_byte_identical_to_local(self):
        ctx = PipeContext(df=_DF)
        EngineNormalizeStage(column="email").run(ctx)
        got = ctx.frame.polars().sort("id")
        ref = _DF.with_columns(
            pl.col("email").str.to_lowercase().str.strip_chars(" ")
        ).sort("id")
        assert got.equals(ref)

    def test_stays_engine_resident_not_materialized(self):
        ctx = PipeContext(df=_DF)
        EngineNormalizeStage(column="email").run(ctx)
        # The result is a lazy DuckDBFrame held on ctx -- NOT pulled into Python.
        assert isinstance(getattr(ctx, "_frame", None), DuckDBFrame)

    def test_chain_stays_in_engine(self):
        # Two remote stages in a row reuse the one engine connection and never
        # materialize between them.
        ctx = PipeContext(df=_DF)
        st = EngineNormalizeStage(column="email")
        st.run(ctx)
        first_con = ctx.metadata["duckdb_con"]
        st.run(ctx)
        assert ctx.metadata["duckdb_con"] is first_con  # same engine
        assert isinstance(getattr(ctx, "_frame", None), DuckDBFrame)


class TestRunnerPhaseC:
    def test_runner_routes_remote_stage(self):
        ctx = PipeContext(df=_DF)
        Runner(registry=StageRegistry()).run(_plan(EngineNormalizeStage("email")), ctx)
        assert isinstance(getattr(ctx, "_frame", None), DuckDBFrame)

    def test_remote_to_local_transition_materializes(self):
        # A local stage after a remote stage must see the materialized df (the
        # Runner pays the egress crossing exactly once, here).
        seen = {}

        @stage(name="local_reader", produces=["df"], consumes=["df"])
        def local_reader(ctx: PipeContext) -> StageResult:
            seen["df_rows"] = None if ctx.df is None else ctx.df.height
            seen["engine_cleared"] = getattr(ctx, "_frame", None) is None
            return StageResult(status=StageStatus.SUCCESS)

        ctx = PipeContext(df=_DF)
        Runner(registry=StageRegistry()).run(
            _plan(EngineNormalizeStage("email"), local_reader), ctx
        )
        assert seen["df_rows"] == _DF.height   # local stage got the data
        assert seen["engine_cleared"] is True  # engine frame materialized + cleared

    def test_plain_remote_marked_stage_still_raises(self):
        @stage(name="plain_remote", produces=["df"], consumes=[], location="remote")
        def plain_remote(ctx: PipeContext) -> StageResult:  # pragma: no cover
            return StageResult(status=StageStatus.SUCCESS)

        with pytest.raises(NotImplementedError, match="not a RemoteStage"):
            Runner(registry=StageRegistry()).run(_plan(plain_remote), PipeContext(df=_DF))
