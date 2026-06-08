"""Top-level ``analyze()`` — resolve analyzers, run them over an artifact, assemble
a single ``AnalysisReport``.

Phase 1 is the generic frame path only. Suite entry points (``analyze_match``,
``analyze_pipeline``) and narrative generation land in Phase 2.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import polars as pl

from goldenanalysis.adapters import FrameArtifactAdapter
from goldenanalysis.models import AnalysisReport
from goldenanalysis.registry import available_analyzers, load_analyzer


def _frame_compatible_analyzers() -> list[str]:
    """Discoverable analyzers that consume a generic ``frame`` and import cleanly.

    Loading is guarded so analyzers needing optional suite deps (Phase 2+) are
    simply skipped from the default set rather than breaking the generic path.
    """
    out: list[str] = []
    for name in available_analyzers():
        try:
            analyzer = load_analyzer(name)
        except Exception:
            continue
        if "frame" in analyzer.info.consumes:
            out.append(name)
    return out


def analyze(
    df: pl.DataFrame,
    analyzers: Sequence[str] | None = None,
    *,
    dataset: str | None = None,
    run_id: str | None = None,
    generated_at: datetime | None = None,
) -> AnalysisReport:
    """Run ``analyzers`` over ``df`` and return a single ``AnalysisReport``.

    ``analyzers=None`` defaults to every frame-compatible analyzer. Names that are
    requested but not discoverable are recorded in ``source["unavailable"]`` rather
    than raising — the report says what it could and couldn't compute.
    """
    inp = FrameArtifactAdapter().load(df, dataset=dataset)
    ds = inp.dataset

    requested = list(analyzers) if analyzers is not None else _frame_compatible_analyzers()
    discoverable = set(available_analyzers())

    ran: list[str] = []
    unavailable: list[str] = []
    metrics = []
    tables = []
    for name in requested:
        if name not in discoverable:
            unavailable.append(name)
            continue
        result = load_analyzer(name).run(inp)
        metrics.extend(result.metrics)
        tables.extend(result.tables)
        ran.append(name)

    gen = generated_at or datetime.now(timezone.utc)
    rid = run_id or f"{gen.isoformat()}#{ds}"
    source = {"dataset": ds, "producer": "frame"}
    if unavailable:
        source["unavailable"] = ",".join(unavailable)

    return AnalysisReport(
        run_id=rid,
        generated_at=gen,
        source=source,
        metrics=metrics,
        tables=tables,
        narrative=None,
        analyzers_run=ran,
    )
