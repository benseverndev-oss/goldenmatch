"""Relocatable-stage contract, Phase C v2: real shipped UDF + engine-resident source.

Design: ``docs/design/2026-07-06-goldenpipe-phasec-baseline-findings.md`` (v2 follow-ons).

v1 proved the RemoteStage mechanism with a hand-written ``lower(trim(...))`` and
a ``LocalFrame`` ingress. v2 closes the two documented gaps:

- **Real UDF** -- ``EngineFlowTransformStage`` runs an actual shipped
  ``goldenflow_*`` DuckDB UDF (from ``goldenmatch_duckdb``) in-engine, byte-
  identical to the goldenflow Python transform.
- **Engine-resident source** -- ``Pipeline.run(duckdb_con=, duckdb_table=)``
  originates the data in DuckDB, so a remote stage right after pays NO ingress
  crossing (proven here by a ``.polars()`` spy that stays at zero).
"""
from __future__ import annotations

import duckdb
import polars as pl
import pytest
from goldenpipe.adapters import LoadStage
from goldenpipe.adapters.engine import (
    EngineFlowTransformStage,
    EngineNormalizeStage,
    RemoteStage,
)
from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import ExecutionPlan, PlannedStage
from goldenpipe.engine.runner import Runner
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import PipeContext, PipeStatus
from goldenpipe.models.frame import DuckDBFrame
from goldenpipe.pipeline import Pipeline

# Skip the whole module unless the shipped goldenflow DuckDB UDFs are available.
_HAS_FLOW_UDFS = True
try:  # pragma: no cover - import guard
    import goldenflow  # noqa: F401
    from goldenmatch_duckdb.goldenflow import (  # noqa: F401
        register_goldenflow_functions,
    )
except ImportError:  # pragma: no cover
    _HAS_FLOW_UDFS = False

pytestmark = pytest.mark.skipif(
    not _HAS_FLOW_UDFS,
    reason="needs goldenmatch-duckdb + goldenflow for the shipped goldenflow_* UDFs",
)

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


def _flow_reference(col: str, transform_name: str) -> list:
    """The goldenflow Python transform applied to a column -- the parity oracle."""
    from goldenflow.transforms import get_transform
    info = get_transform(transform_name)
    out = info.func(pl.Series(_DF[col]))
    return out.to_list()


class TestEngineFlowTransformStage:
    def test_is_remote_capable(self):
        st = EngineFlowTransformStage(column="email", transform="email")
        assert isinstance(st, RemoteStage)
        assert st.remote_capable is True
        assert st.info.location == "remote"
        assert st.info.name == "engine.flow.email"

    def test_unknown_transform_raises(self):
        with pytest.raises(ValueError, match="unknown flow transform"):
            EngineFlowTransformStage(column="email", transform="nope")

    def test_byte_identical_to_goldenflow_python(self):
        ctx = PipeContext(df=_DF)
        st = EngineFlowTransformStage(column="email", transform="email")
        st.validate(ctx)
        st.run(ctx)
        got = ctx.frame.polars().sort("id")["email"].to_list()
        assert got == _flow_reference("email", "email_normalize")

    def test_stays_engine_resident(self):
        ctx = PipeContext(df=_DF)
        st = EngineFlowTransformStage(column="email", transform="email")
        st.run(ctx)
        # Result is a lazy DuckDBFrame held on ctx -- not pulled into Python.
        assert isinstance(getattr(ctx, "_frame", None), DuckDBFrame)

    def test_chains_with_normalize_in_one_engine(self):
        # A goldenflow-UDF stage then a plain-SQL stage reuse the same engine
        # connection; the flow UDFs are registered exactly once.
        ctx = PipeContext(df=_DF)
        EngineFlowTransformStage(column="email", transform="strip").run(ctx)
        first_con = ctx.metadata["duckdb_con"]
        assert ctx.metadata.get("_goldenflow_udfs_registered") is True
        EngineNormalizeStage(column="email").run(ctx)
        assert ctx.metadata["duckdb_con"] is first_con
        assert isinstance(getattr(ctx, "_frame", None), DuckDBFrame)


class TestRunnerRoutesFlowStage:
    def test_runner_routes_and_materializes(self):
        ctx = PipeContext(df=_DF)
        Runner(registry=StageRegistry()).run(
            _plan(EngineFlowTransformStage("email", "email")), ctx
        )
        assert isinstance(getattr(ctx, "_frame", None), DuckDBFrame)
        assert ctx.frame.polars().sort("id")["email"].to_list() == _flow_reference(
            "email", "email_normalize"
        )


def _load_only_pipeline() -> Pipeline:
    reg = StageRegistry()
    reg.register(LoadStage())
    return Pipeline(config=PipelineConfig(pipeline="t", stages=[]), registry=reg)


class TestDuckDBTableSource:
    def test_pipeline_run_loads_from_duckdb_table(self):
        # End-to-end through Pipeline.run: a minimal load-only pipeline sources
        # from a DuckDB table (materializes at the local `load` stage) and
        # reports the right rows + source label.
        con = duckdb.connect()
        con.register("df_view", _DF.to_arrow())
        con.execute("CREATE TABLE people AS SELECT * FROM df_view")

        result = _load_only_pipeline().run(duckdb_con=con, duckdb_table="people")

        assert result.status == PipeStatus.SUCCESS
        assert result.input_rows == 3
        assert result.source == "duckdb:people"

    def test_invalid_table_name_fails_cleanly(self):
        con = duckdb.connect()
        result = _load_only_pipeline().run(
            duckdb_con=con, duckdb_table="bad; DROP TABLE x"
        )
        assert result.status == PipeStatus.FAILED
        assert any("Invalid DuckDB table name" in e for e in result.errors)

    def test_remote_stage_after_source_pays_no_ingress(self):
        # THE v2 WIN: data originates in DuckDB, the first stage is remote, so
        # the frame is never materialized to Python (no ingress crossing). A
        # `.polars()` spy on the seeded frame stays at zero across the stage.
        con = duckdb.connect()
        con.register("df_view", _DF.to_arrow())
        con.execute("CREATE TABLE people AS SELECT * FROM df_view")
        rel = con.sql("SELECT * FROM people")

        materialized = {"n": 0}

        class SpyFrame(DuckDBFrame):
            def polars(self):  # noqa: D401
                materialized["n"] += 1
                return super().polars()

        ctx = PipeContext()
        ctx.frame = SpyFrame(rel)  # engine-resident seed (as Pipeline.run does)
        ctx.metadata["duckdb_con"] = con

        Runner(registry=StageRegistry()).run(
            _plan(EngineFlowTransformStage("email", "email")), ctx
        )

        # The seeded frame was NEVER materialized -> no ingress crossing.
        assert materialized["n"] == 0
        assert ctx.df is None  # nothing pulled to Python during the remote stage
        # ...and the result is still byte-correct at egress.
        assert ctx.frame.polars().sort("id")["email"].to_list() == _flow_reference(
            "email", "email_normalize"
        )
