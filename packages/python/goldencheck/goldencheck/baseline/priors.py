"""Confidence prior builder for deep profiling baseline."""
from __future__ import annotations

from goldencheck.baseline.models import ConfidencePrior
from goldencheck.models.finding import Finding

__all__ = ["build_priors", "apply_prior"]


def build_priors(findings: list[Finding], row_count: int) -> dict[str, dict[str, ConfidencePrior]]:
    """Build confidence priors from a list of findings.

    Groups findings by (check, column), averages the confidence per group, and
    returns a nested dict ``{check_name: {column: ConfidencePrior}}``.

    Args:
        findings: List of Finding instances from a validation run.
        row_count: Number of rows in the dataset — used as evidence_count for all priors.

    Returns:
        Nested dict of priors, or an empty dict if *findings* is empty.
    """
    if not findings:
        return {}

    # Accumulate (sum, count) per (check, column)
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}

    for f in findings:
        key = (f.check, f.column)
        sums[key] = sums.get(key, 0.0) + f.confidence
        counts[key] = counts.get(key, 0) + 1

    result: dict[str, dict[str, ConfidencePrior]] = {}
    for (check, column), total in sums.items():
        avg_confidence = total / counts[(check, column)]
        if check not in result:
            result[check] = {}
        result[check][column] = ConfidencePrior(confidence=avg_confidence, evidence_count=row_count)

    return result


def apply_prior(raw_confidence: float, prior: ConfidencePrior) -> float:
    """Adjust a raw confidence value toward a prior.

    Formula::

        prior_weight = min(prior.evidence_count / 100, 1.0)
        adjusted = (raw * evidence_weight + prior.confidence * prior_weight)
                   / (evidence_weight + prior_weight)

    where ``evidence_weight = 1.0``.

    The result is clamped to ``[0.0, 1.0]`` and rounded to 4 decimal places.

    Args:
        raw_confidence: Raw confidence from the current check (may be outside [0, 1]
            in edge cases; will be clamped after adjustment).
        prior: The ConfidencePrior to blend toward.

    Returns:
        Adjusted confidence, clamped to [0.0, 1.0] and rounded to 4 d.p.
    """
    evidence_weight = 1.0
    prior_weight = min(prior.evidence_count / 100, 1.0)

    adjusted = (raw_confidence * evidence_weight + prior.confidence * prior_weight) / (
        evidence_weight + prior_weight
    )

    clamped = max(0.0, min(1.0, adjusted))
    return round(clamped, 4)
