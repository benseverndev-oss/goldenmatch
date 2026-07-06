"""Remote (in-engine) stages -- relocatable-stage contract, Phase C.

Phase C's baseline (`docs/design/2026-07-06-goldenpipe-phasec-baseline-findings.md`)
showed the engine boundary is a *real* cost (the DuckDB<->Python crossing was ~89%
of the pull path at 5M rows), so keeping a stage in-engine pays. This module is the
first ``location="remote"`` stage that actually runs there instead of raising.

``RemoteStage`` is the marker the Runner routes on (``remote_capable = True``); a
remote stage reads ``ctx.frame``, transforms IN THE ENGINE, and hands back an
engine-resident ``DuckDBFrame`` (kept lazy). A chain of remote stages stays in the
engine -- the Python round-trip is paid once, at egress, only when a local stage
next needs the data (the Runner materializes it there).

Scope (v1): ``EngineNormalizeStage`` demonstrates the mechanism with a plain SQL
projection.

Scope (v2): ``EngineFlowTransformStage`` wires a **real shipped** ``goldenflow_*``
DuckDB UDF (from ``goldenmatch_duckdb``, the same kernel exposed on the DuckDB /
Postgres / dbt surfaces) behind this same contract; and ``Pipeline.run`` gains a
DuckDB-table *source* (``duckdb_con`` + ``duckdb_table``) so the data originates
in-engine -- a remote stage right after it pays **no** ingress crossing.

Honest caveat (v2): the DuckDB ``goldenflow_*`` UDFs are per-value **Python**
callbacks (in-process polars), NOT the compiled zero-Python ``goldenflow-duckdb``
cdylib. So this does not remove Python from the per-value path; what it removes
is the DataFrame **materialization** boundary *between* stages -- the ~89% pull
Phase C measured -- because the relation stays lazy and engine-resident across
the chain. The dominant ``goldenmatch.dedupe`` scoring stage still has no
in-engine surface, so a full ER pipeline crosses for it.
"""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.frame import DuckDBFrame
from goldenpipe.models.stage import StageInfo


class RemoteStage:
    """Base marker for a stage whose compute runs on a remote engine (Phase C).

    The Runner routes ``remote_capable`` stages to ``run()`` instead of raising
    ``NotImplementedError`` (a plain ``location="remote"`` stage without this
    marker still raises -- the Phase A guard). Subclasses run in-engine and leave
    an engine-resident frame on ``ctx``.
    """

    remote_capable = True
    rollback = None

    def validate(self, ctx: PipeContext) -> None:  # noqa: D401 - protocol hook
        pass


class EngineNormalizeStage(RemoteStage):
    """Normalize a text column **in DuckDB**: ``lower(trim(col))``, other columns
    passed through. Logically identical to a Polars ``lower + strip`` stage, but
    the data never leaves the engine -- so a chain of these avoids the pull-to-
    Python crossing Phase C measured.

    The engine connection is reused across remote stages via
    ``ctx.metadata["duckdb_con"]`` (so their lazy relations chain in ONE engine).
    """

    def __init__(self, column: str = "email", con=None) -> None:
        self.column = column
        self._con = con
        self.info = StageInfo(
            name="engine.normalize", produces=["df"], consumes=["df"], location="remote"
        )

    def run(self, ctx: PipeContext) -> StageResult:
        import duckdb

        frame = ctx.frame
        con = self._con or ctx.metadata.get("duckdb_con")
        if con is None:
            con = duckdb.connect()
            ctx.metadata["duckdb_con"] = con

        if isinstance(frame, DuckDBFrame):
            # Already engine-resident: chain in-engine, no crossing.
            ddf = frame
        else:
            # Ingress: the data is in Python (a LocalFrame over ctx.df). Load it
            # into the engine once. (For a warehouse-resident source -- the v2
            # DuckDB-table source -- this ingress disappears entirely.)
            ddf = DuckDBFrame(con.from_arrow(frame.polars().to_arrow()))

        cols = ddf.relation().columns
        projection = ", ".join(
            f"lower(trim({c})) AS {c}" if c == self.column else c for c in cols
        )
        # Stays a lazy DuckDBFrame -> stored on ctx without materializing.
        ctx.frame = ddf.project(projection)
        return StageResult(status=StageStatus.SUCCESS)


# Friendly alias -> the shipped ``goldenmatch_duckdb`` UDF name. These are the
# exact UDFs ``register_goldenflow_functions`` puts on a DuckDB connection.
_FLOW_UDF_BY_ALIAS = {
    "email": "goldenflow_normalize_email",
    "phone": "goldenflow_normalize_phone",
    "date": "goldenflow_normalize_date",
    "name": "goldenflow_normalize_name_proper",
    "url": "goldenflow_canonicalize_url",
    "address": "goldenflow_canonicalize_address",
    "strip": "goldenflow_strip",
    "whitespace": "goldenflow_whitespace_normalize",
}


class EngineFlowTransformStage(RemoteStage):
    """Apply a **real shipped** ``goldenflow_*`` DuckDB UDF to a column **in the
    engine** (Phase C v2).

    Where ``EngineNormalizeStage`` proved the mechanism with a hand-written
    ``lower(trim(...))``, this wires an actual cross-surface kernel: the same
    ``goldenflow_normalize_email`` / ``goldenflow_strip`` / ... that
    ``goldenmatch_duckdb`` registers for the DuckDB SQL surface (and whose
    Postgres / dbt siblings share the goldenflow transform registry). The UDF is
    applied as an in-engine projection, so the frame stays a lazy
    ``DuckDBFrame`` -- a chain of these avoids the pull-to-Python between stages.

    ``transform`` is a friendly alias (``"email"``, ``"strip"``, ...); see
    ``_FLOW_UDF_BY_ALIAS``. The UDFs are registered ONCE per engine connection
    (idempotent-guarded on ``ctx.metadata``), reusing the shared
    ``ctx.metadata["duckdb_con"]`` so remote stages chain in one engine.

    Requires ``goldenmatch-duckdb`` (for the registration entrypoint) and
    ``goldenflow`` (for the transforms) to be installed; ``validate`` raises a
    clear, actionable error otherwise rather than silently passing values
    through.
    """

    def __init__(self, column: str, transform: str = "email", con=None) -> None:
        if transform not in _FLOW_UDF_BY_ALIAS:
            raise ValueError(
                f"unknown flow transform {transform!r}; "
                f"expected one of {sorted(_FLOW_UDF_BY_ALIAS)}"
            )
        self.column = column
        self.transform = transform
        self._udf = _FLOW_UDF_BY_ALIAS[transform]
        self._con = con
        self.info = StageInfo(
            name=f"engine.flow.{transform}",
            produces=["df"], consumes=["df"], location="remote",
        )

    def validate(self, ctx: PipeContext) -> None:
        try:
            import goldenflow  # noqa: F401
            from goldenmatch_duckdb.goldenflow import (  # noqa: F401
                register_goldenflow_functions,
            )
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "EngineFlowTransformStage needs the shipped goldenflow DuckDB "
                "UDFs. Install both: `pip install goldenmatch-duckdb goldenflow`."
            ) from exc

    def run(self, ctx: PipeContext) -> StageResult:
        import duckdb

        frame = ctx.frame
        con = self._con or ctx.metadata.get("duckdb_con")
        if con is None:
            con = duckdb.connect()
            ctx.metadata["duckdb_con"] = con

        # Register the shipped goldenflow_* UDFs once per engine connection.
        if not ctx.metadata.get("_goldenflow_udfs_registered"):
            from goldenmatch_duckdb.goldenflow import register_goldenflow_functions
            register_goldenflow_functions(con)
            ctx.metadata["_goldenflow_udfs_registered"] = True

        if isinstance(frame, DuckDBFrame):
            ddf = frame  # already engine-resident: chain in-engine, no crossing
        else:
            # Ingress from Python (a LocalFrame over ctx.df) -- paid once. With
            # the v2 DuckDB-table source this branch never runs.
            ddf = DuckDBFrame(con.from_arrow(frame.polars().to_arrow()))

        cols = ddf.relation().columns
        projection = ", ".join(
            f'{self._udf}("{c}") AS "{c}"' if c == self.column else f'"{c}"'
            for c in cols
        )
        ctx.frame = ddf.project(projection)
        return StageResult(status=StageStatus.SUCCESS)
