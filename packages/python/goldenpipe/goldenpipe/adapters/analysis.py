"""GoldenAnalysis terminal reporting stage -- Phase 5.

Runs goldenanalysis over the pipeline's accumulated artifacts (clusters /
scored_pairs / match_stats / findings / manifest -- the SAME keys the upstream
stages already surface, so no remapping is needed) and attaches a unified
``analysis_report``. READ-ONLY: it writes only that one artifact and never mutates
the data or any store. Optional -- ships behind ``goldenpipe[analysis]`` and
degrades to a clear error when goldenanalysis isn't installed.

Makes "one CLI runs Check -> Flow -> Match -> Identity -> Analysis" literally true.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

try:
    from goldenanalysis import analyze_pipeline

    HAS_ANALYSIS = True
except ImportError:
    HAS_ANALYSIS = False
    analyze_pipeline = None  # type: ignore[assignment]


class AnalysisReportStage:
    info = StageInfo(
        name="goldenanalysis.report",
        produces=["analysis_report"],
        # Only `df` is hard-required (always present, so wiring never fails).
        # clusters / scored_pairs / match_stats / findings / manifest /
        # identity_summary are read opportunistically from ctx.artifacts --
        # goldenanalysis degrades to whatever is present, so the stage works on
        # any pipeline (check-only, full dedupe, etc.). Place it LAST in the
        # config to report over every upstream artifact.
        consumes=["df"],
    )
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_ANALYSIS:
            raise RuntimeError(
                "goldenanalysis not available. Install it with "
                "`pip install goldenpipe[analysis]`."
            )

    def run(self, ctx: PipeContext) -> StageResult:
        # A PipeResult-like view over the accumulated artifacts. goldenanalysis's
        # pipe adapter reads `.artifacts` + `.source`; it fans out to every analyzer
        # whose consumed artifacts are present and skips the rest.
        result_like = SimpleNamespace(
            artifacts=dict(ctx.artifacts),
            source=ctx.metadata.get("source"),
        )
        try:
            report = analyze_pipeline(result_like)
        except Exception as e:  # noqa: BLE001 - a reporting stage must never break the run
            logger.warning("Analysis reporting failed: %s", e)
            return StageResult(status=StageStatus.FAILED, error=str(e))

        # Attach as a plain JSON dict (read-only; never written back to the data).
        ctx.artifacts["analysis_report"] = json.loads(report.to_json())
        logger.info(
            "Analysis report: %d metric(s) from [%s]",
            len(report.metrics),
            ",".join(report.analyzers_run) or "no analyzers",
        )
        return StageResult(status=StageStatus.SUCCESS)
