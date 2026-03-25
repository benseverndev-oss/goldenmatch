"""Adaptive pipeline decisions -- pure logic, no tool imports."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FlowDecision:
    skip: bool
    reason: str
    abort: bool = False
    findings: list = field(default_factory=list)


@dataclass
class MatchDecision:
    strategy: str = "auto"
    skip: bool = False
    reason: str = ""


def decide_flow(check_result) -> FlowDecision:
    """Decide whether to run GoldenFlow based on check findings."""
    if check_result is None:
        return FlowDecision(skip=True, reason="Check was skipped or failed")

    findings = getattr(check_result, "findings", []) or []
    if not findings:
        return FlowDecision(skip=True, reason="No quality issues found")

    fatal = [f for f in findings if getattr(f, "severity", "") == "critical"]
    fixable = [f for f in findings if getattr(f, "severity", "") != "critical"]

    if fatal and not fixable:
        return FlowDecision(
            skip=True, abort=True,
            reason=f"Fatal issues only: {[getattr(f, 'check', str(f)) for f in fatal]}",
        )

    return FlowDecision(
        skip=False,
        findings=fixable,
        reason=f"{len(fixable)} fixable issues found",
    )


def decide_match(check_result, row_count: int, strategy_override: str | None = None) -> MatchDecision:
    """Decide matching strategy based on check results and data profile."""
    if strategy_override:
        return MatchDecision(strategy=strategy_override, reason=f"User specified: {strategy_override}")

    if row_count < 2:
        return MatchDecision(skip=True, reason=f"Only {row_count} rows -- nothing to deduplicate")

    findings = getattr(check_result, "findings", []) or [] if check_result else []
    sensitive = any(getattr(f, "check", "") == "pii_detection" for f in findings)
    if sensitive:
        return MatchDecision(strategy="pprl", reason="Sensitive fields detected")

    return MatchDecision(strategy="auto", reason="Auto-detect strategy from data")
