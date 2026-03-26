"""Built-in decision functions for pipeline routing."""
from __future__ import annotations

from goldenpipe.models.context import Decision, PipeContext


def severity_gate(ctx: PipeContext) -> Decision | None:
    """Abort pipeline if any finding has critical severity."""
    findings = ctx.artifacts.get("findings")
    if not findings:
        return None

    has_critical = any(f.get("severity") == "critical" for f in findings)
    if has_critical:
        return Decision(abort=True, reason="Critical findings detected")
    return None


def pii_router(ctx: PipeContext) -> Decision | None:
    """Route to PPRL matching if PII is detected."""
    findings = ctx.artifacts.get("findings")
    if not findings:
        return None

    has_pii = any(f.get("check") == "pii_detection" for f in findings)
    if has_pii:
        return Decision(
            skip=["goldenmatch.dedupe"],
            insert=["goldenmatch.dedupe_pprl"],
            reason="PII detected, routing to PPRL matching",
        )
    return None


def row_count_gate(ctx: PipeContext) -> Decision | None:
    """Skip matching if fewer than 2 rows."""
    row_count = ctx.metadata.get("input_rows", 0)
    if row_count < 2:
        return Decision(
            skip=["goldenmatch.dedupe"],
            reason=f"Only {row_count} row(s), skipping deduplication",
        )
    return None
