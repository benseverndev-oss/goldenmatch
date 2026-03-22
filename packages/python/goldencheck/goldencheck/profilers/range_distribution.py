"""Range and distribution profiler — detects outliers and reports min/max for numeric columns."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

NUMERIC_DTYPES = (
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Float32, pl.Float64,
)


class RangeDistributionProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]

        if col.dtype not in NUMERIC_DTYPES:
            return findings

        non_null = col.drop_nulls()
        total = len(non_null)
        if total < 2:
            return findings

        mean = non_null.mean()
        std = non_null.std()
        col_min = non_null.min()
        col_max = non_null.max()

        findings.append(Finding(
            severity=Severity.INFO,
            column=column,
            check="range_distribution",
            message=f"Range: min={col_min}, max={col_max}, mean={mean:.2f}",
        ))

        if std is not None and std > 0:
            lower = mean - 3 * std
            upper = mean + 3 * std
            outliers = non_null.filter((non_null < lower) | (non_null > upper))
            outlier_count = len(outliers)
            if outlier_count > 0:
                sample = outliers.head(5).to_list()
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=column,
                    check="range_distribution",
                    message=f"{outlier_count} outlier(s) detected beyond 3 standard deviations",
                    affected_rows=outlier_count,
                    sample_values=[str(v) for v in sample],
                    suggestion="Investigate outlier values for data entry errors or anomalies",
                ))

        return findings
