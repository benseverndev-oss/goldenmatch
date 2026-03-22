"""Null correlation profiler — detects columns whose null patterns are highly correlated."""
from __future__ import annotations

from itertools import combinations

import polars as pl

from goldencheck.models.finding import Finding, Severity

_DEFAULT_THRESHOLD = 0.90


class NullCorrelationProfiler:
    """Reports pairs of columns whose null/non-null patterns agree >= threshold fraction of rows."""

    def __init__(self, threshold: float = _DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        findings: list[Finding] = []
        columns = df.columns
        n_rows = len(df)

        if n_rows == 0 or len(columns) < 2:
            return findings

        # Pre-compute null masks (True where null) as Python lists for fast comparison
        null_masks: dict[str, pl.Series] = {
            col: df[col].is_null() for col in columns
        }

        for col_a, col_b in combinations(columns, 2):
            mask_a = null_masks[col_a]
            mask_b = null_masks[col_b]

            # Skip pairs where neither column has any nulls — no interesting correlation
            null_count_a = mask_a.sum()
            null_count_b = mask_b.sum()
            if null_count_a == 0 and null_count_b == 0:
                continue

            # Agreement: rows where both are null or both are non-null
            agreement = (mask_a == mask_b).sum()
            correlation = agreement / n_rows

            if correlation >= self.threshold:
                findings.append(
                    Finding(
                        severity=Severity.INFO,
                        column=f"{col_a},{col_b}",
                        check="null_correlation",
                        message=(
                            f"Columns '{col_a}' and '{col_b}' have strongly correlated null patterns "
                            f"({correlation:.1%} agreement). They may represent a logical group."
                        ),
                        affected_rows=int(null_count_a),
                        suggestion=(
                            f"Consider treating '{col_a}' and '{col_b}' as a unit — "
                            "validate that they are both populated or both absent together."
                        ),
                    )
                )

        return findings
