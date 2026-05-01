"""Post-scan confidence processing."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
from goldencheck.models.finding import Finding, Severity

__all__ = ["apply_confidence_downgrade", "apply_corroboration_boost"]


def apply_corroboration_boost(findings: list[Finding]) -> list[Finding]:
    """Boost confidence for columns flagged by multiple profilers.

    - 2 distinct WARNING/ERROR checks on the same column: +0.1
    - 3+ distinct WARNING/ERROR checks on the same column: +0.2 (exclusive tiers)
    - Capped at 1.0
    - Uses dataclasses.replace() — original findings are never mutated.
    """
    # Count distinct WARNING/ERROR checks per column
    checks_per_col: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        if f.severity in (Severity.ERROR, Severity.WARNING):
            checks_per_col[f.column].add(f.check)

    result = []
    for f in findings:
        col_count = len(checks_per_col.get(f.column, set()))
        if col_count >= 3:
            boost = 0.2
        elif col_count >= 2:
            boost = 0.1
        else:
            boost = 0.0

        if boost > 0 and f.severity in (Severity.ERROR, Severity.WARNING):
            new_conf = min(f.confidence + boost, 1.0)
            result.append(replace(f, confidence=new_conf))
        else:
            result.append(f)
    return result


def apply_confidence_downgrade(findings: list[Finding], llm_boost: bool) -> list[Finding]:
    """Downgrade low-confidence findings to INFO when LLM boost is not enabled.

    If llm_boost=True, return findings unchanged (LLM will verify them).
    If llm_boost=False, any WARNING/ERROR with confidence < 0.5 is downgraded to INFO
    with a suffix explaining how to get better accuracy.
    """
    if llm_boost:
        return list(findings)

    result = []
    for f in findings:
        if f.confidence < 0.5 and f.severity in (Severity.ERROR, Severity.WARNING):
            result.append(replace(
                f,
                severity=Severity.INFO,
                message=f"{f.message} (low confidence — use --llm-boost to verify)",
            ))
        else:
            result.append(f)
    return result
