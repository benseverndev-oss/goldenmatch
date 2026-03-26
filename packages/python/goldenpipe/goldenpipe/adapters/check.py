"""GoldenCheck adapter -- wraps scan_file()."""
from __future__ import annotations

from goldenpipe.models.context import PipeContext, StageResult, StageStatus
from goldenpipe.models.stage import StageInfo

try:
    from goldencheck import scan_file as _scan
    HAS_CHECK = True
except ImportError:
    HAS_CHECK = False
    _scan = None


class ScanStage:
    info = StageInfo(name="goldencheck.scan", produces=["findings"], consumes=["df"])
    rollback = None

    def validate(self, ctx: PipeContext) -> None:
        if not HAS_CHECK:
            raise RuntimeError("GoldenCheck not installed. Run: pip install goldenpipe[check]")

    def run(self, ctx: PipeContext) -> StageResult:
        source = ctx.metadata.get("source", "")
        result = _scan(source)
        findings = []
        if hasattr(result, "findings"):
            for f in result.findings:
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
        return StageResult(status=StageStatus.SUCCESS)
