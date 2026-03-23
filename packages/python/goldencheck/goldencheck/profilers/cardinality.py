"""Cardinality profiler — detects low-cardinality columns that may be enum candidates."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

ENUM_UNIQUE_THRESHOLD = 20
ENUM_MIN_ROWS = 50


class CardinalityProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        total = len(col)
        unique_count = col.n_unique()

        if unique_count < ENUM_UNIQUE_THRESHOLD and total >= ENUM_MIN_ROWS:
            unique_vals = col.drop_nulls().unique().sort().to_list()
            sample = [str(v) for v in unique_vals[:10]]
            # <10 unique in 1000+ rows → 0.9; 10-20 in 50-100 → 0.5
            if unique_count < 10 and total >= 1000:
                confidence = 0.9
            elif 10 <= unique_count < 20 and 50 <= total <= 100:
                confidence = 0.5
            else:
                confidence = 0.7
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="cardinality",
                message=(
                    f"Low cardinality: {unique_count} unique value(s) across {total} rows — "
                    f"consider using an enum type"
                ),
                affected_rows=total,
                sample_values=sample,
                suggestion="Define an enum or categorical constraint for this column",
                confidence=confidence,
            ))
        else:
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="cardinality",
                message=f"Cardinality: {unique_count} unique value(s) across {total} rows",
            ))

        return findings
