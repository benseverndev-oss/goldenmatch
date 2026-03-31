"""GoldenCheck adapter -- wraps scan_file()."""
from __future__ import annotations

import logging

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

logger = logging.getLogger(__name__)

try:
    from goldencheck import scan_file as _scan
    HAS_CHECK = True
except ImportError:
    HAS_CHECK = False
    _scan = None


class ScanStage:
    info = StageInfo(name="goldencheck.scan", produces=["findings", "profile"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_CHECK:
            raise RuntimeError("GoldenCheck not installed. Run: pip install goldenpipe[check]")

    def run(self, ctx: PipeContext) -> StageResult:
        source = ctx.metadata.get("source", "")
        result = _scan(source)

        # scan_file returns (findings, profile) tuple
        if isinstance(result, tuple) and len(result) >= 2:
            raw_findings, profile = result[0], result[1]
        else:
            raw_findings = result.findings if hasattr(result, "findings") else []
            profile = None
            logger.warning(
                "ScanStage: scan_file returned %s (expected tuple). "
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

        return StageResult(status=StageStatus.SUCCESS)
