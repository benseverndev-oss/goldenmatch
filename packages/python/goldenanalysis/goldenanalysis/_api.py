"""Top-level analyze entrypoints — resolve analyzers, run them over an artifact,
assemble a single ``AnalysisReport``.

- ``analyze(df, ...)`` — the generic frame path (Phase 1).
- ``analyze_match(result, ...)`` / ``analyze_pipeline(result)`` — suite paths
  (Phase 2a) over a GoldenMatch ``DedupeResult`` / GoldenPipe ``PipeResult``.

Cross-run aggregation + narrative generation land in Phase 2b.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import polars as pl

from goldenanalysis.adapters import FrameArtifactAdapter
from goldenanalysis.models import AnalysisReport, AnalyzerInput
from goldenanalysis.registry import available_analyzers, load_analyzer


def _assemble_report(
    inp: AnalyzerInput,
    analyzer_names: Sequence[str],
    *,
    run_id: str | None = None,
    generated_at: datetime | None = None,
) -> AnalysisReport:
    """Run ``analyzer_names`` over ``inp`` and assemble one ``AnalysisReport``.

    Shared by every analyze entrypoint. Names that are requested but not
    discoverable are recorded in ``source["unavailable"]`` rather than raising.
    """
    ds = inp.dataset
    discoverable = set(available_analyzers())

    ran: list[str] = []
    unavailable: list[str] = []
    metrics = []
    tables = []
    for name in analyzer_names:
        if name not in discoverable:
            unavailable.append(name)
            continue
        result = load_analyzer(name).run(inp)
        metrics.extend(result.metrics)
        tables.extend(result.tables)
        ran.append(name)

    gen = generated_at or datetime.now(UTC)
    rid = run_id or f"{gen.isoformat()}#{ds}"
    source = {"dataset": ds, "producer": inp.artifacts.get("__producer__", "frame")}
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


def _frame_compatible_analyzers() -> list[str]:
    """Discoverable analyzers that consume a generic ``frame`` and import cleanly.

    Loading is guarded so analyzers needing optional suite deps are simply skipped
    from the default set rather than breaking the generic path.
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


def _artifact_compatible_analyzers(inp: AnalyzerInput) -> list[str]:
    """Discoverable analyzers at least one of whose ``consumes`` keys is present
    in ``inp.artifacts`` — the fan-out selector for ``analyze_pipeline``."""
    present = set(inp.artifacts)
    out: list[str] = []
    for name in available_analyzers():
        try:
            analyzer = load_analyzer(name)
        except Exception:
            continue
        if any(key in present for key in analyzer.info.consumes):
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
    requested = list(analyzers) if analyzers is not None else _frame_compatible_analyzers()
    return _assemble_report(inp, requested, run_id=run_id, generated_at=generated_at)


def analyze_match(
    result: Any,
    *,
    dataset: str | None = None,
    certificate: Any = None,
    run_id: str | None = None,
    generated_at: datetime | None = None,
) -> AnalysisReport:
    """Analyze a GoldenMatch ``DedupeResult``: ``match.rates`` + ``cluster.distribution``.

    ``certificate`` (optional) is a recall certificate — a ``{estimate, safe_bound}``
    dict or a ``RecallEstimate``/``RecallCertificate``. When absent, the recall
    metrics are omitted (graceful degradation).
    """
    from goldenanalysis.adapters.match import MatchArtifactAdapter

    inp = MatchArtifactAdapter().load(result, dataset=dataset, certificate=certificate)
    return _assemble_report(
        inp, ["match.rates", "cluster.distribution"], run_id=run_id, generated_at=generated_at
    )


def analyze_pipeline(
    result: Any,
    *,
    run_id: str | None = None,
    generated_at: datetime | None = None,
) -> AnalysisReport:
    """Analyze a GoldenPipe ``PipeResult``, fanning out to every analyzer whose
    consumed artifacts are present in ``result.artifacts``."""
    from goldenanalysis.adapters.pipe import PipeArtifactAdapter

    inp = PipeArtifactAdapter().load(result)
    names = _artifact_compatible_analyzers(inp)
    return _assemble_report(inp, names, run_id=run_id, generated_at=generated_at)
