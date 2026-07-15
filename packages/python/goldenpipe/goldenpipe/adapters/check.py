"""GoldenCheck adapter -- scans the in-memory frame (scan_dataframe), falling
back to scan_file() only when no frame is available."""
from __future__ import annotations

import logging

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

try:
    from goldencheck import scan_dataframe as _scan_df
    from goldencheck import scan_file as _scan
    HAS_CHECK = True
except ImportError:
    HAS_CHECK = False
    _scan = None
    _scan_df = None


class ScanStage:
    info = StageInfo(name="goldencheck.scan", produces=["findings", "profile"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_CHECK:
            raise RuntimeError("GoldenCheck not installed. Run: pip install goldenpipe[check]")

    def run(self, ctx: PipeContext) -> StageResult:
        source = ctx.metadata.get("source", "")
        stage_cfg = ctx.stage_config
        if ctx.df is not None:
            # Scan the frame the pipeline already loaded -- no redundant disk
            # re-read, and it works for DataFrame/DuckDB sources whose `source`
            # string ("<DataFrame>"/"duckdb:...") is not a readable path.
            # `file_path` is cosmetic (populates DatasetProfile.file_path).
            if stage_cfg:
                logger.info("Passing stage config to GoldenCheck scan_dataframe")
                result = _scan_df(ctx.df, file_path=source, **stage_cfg)
            else:
                result = _scan_df(ctx.df, file_path=source)
        else:
            # Defensive fallback: no in-memory frame. A local stage normally has
            # ctx.df materialized by the Runner before it runs, so this path is
            # rare; keep the file scan for it.
            logger.info("ScanStage: no ctx.df; falling back to scan_file(%s)", source)
            result = _scan(source, **stage_cfg) if stage_cfg else _scan(source)

        # scan_dataframe/scan_file both return a (findings, profile) tuple
        if isinstance(result, tuple) and len(result) >= 2:
            raw_findings, profile = result[0], result[1]
        else:
            raw_findings = result.findings if hasattr(result, "findings") else []
            profile = None
            logger.warning(
                "ScanStage: scan returned %s (expected tuple). "
                "Profile will be None — column context pipeline may not produce contexts.",
                type(result).__name__,
            )

        if not isinstance(raw_findings, (list, tuple)):
            logger.warning("ScanStage: raw_findings is %s, treating as empty", type(raw_findings).__name__)
            raw_findings = []

        findings = []
        for f in raw_findings:
            if isinstance(f, dict):
                findings.append(f)
            else:
                findings.append({
                    "severity": getattr(f, "severity", "info"),
                    "check": getattr(f, "check", "unknown"),
                    "column": getattr(f, "column", ""),
                    "message": getattr(f, "message", ""),
                })

        ctx.artifacts["findings"] = findings
        ctx.artifacts["profile"] = profile

        # Build column contexts for downstream stages (best-effort enrichment)
        try:
            from goldenpipe.models.column_context import build_contexts_from_check
            ctx.artifacts["column_contexts"] = build_contexts_from_check(raw_findings, profile)
        except Exception:
            logger.exception("Failed to build column contexts; downstream stages will auto-configure")
            ctx.artifacts["column_contexts"] = []

        # Advisory repair-plan (never mutates the stage list; failures are non-fatal)
        try:
            from goldenpipe.repair_host import attach_repair_plan
            _findings = ctx.artifacts.get("findings", [])
            _contexts = ctx.artifacts.get("column_contexts", [])
            if ctx.df is not None and _findings and _contexts:
                attach_repair_plan(ctx, _findings, _contexts, ctx.df)
        except Exception:
            logger.exception("repair-plan attach failed; advisory artifact skipped")

        return StageResult(status=StageStatus.SUCCESS)
