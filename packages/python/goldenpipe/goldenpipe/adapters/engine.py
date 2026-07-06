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
projection. Wiring the real ``goldenflow_*`` / ``goldencheck_*`` DuckDB UDFs
(already shipped from the cross-surface work) behind this same contract, and a
DuckDB-table *source* for ``Pipeline.run`` so the data originates in-engine, are
the v2 follow-ons.
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
