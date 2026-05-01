"""Auto-triage engine — classify findings into pin/dismiss/review buckets."""
from __future__ import annotations

from dataclasses import dataclass, field

from goldencheck.models.finding import Finding, Severity

__all__ = ["auto_triage", "TriageResult"]


@dataclass
class TriageResult:
    pin: list[Finding] = field(default_factory=list)
    dismiss: list[Finding] = field(default_factory=list)
    review: list[Finding] = field(default_factory=list)


def auto_triage(findings: list[Finding]) -> TriageResult:
    """Classify findings into pin/dismiss/review buckets.

    Operates on POST-downgrade findings (after apply_confidence_downgrade).
    """
    result = TriageResult()

    for f in findings:
        if f.severity >= Severity.WARNING and f.confidence >= 0.8:
            result.pin.append(f)
        elif f.severity == Severity.INFO or f.confidence < 0.5:
            result.dismiss.append(f)
        else:
            result.review.append(f)

    return result
