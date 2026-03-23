"""Sequence gap detection profiler — detects gaps in sequential integer columns."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

INTEGER_DTYPES = (
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
)

# Minimum fraction of consecutive diffs == 1 to consider column sequential.
# We use this threshold on columns where the values increment exactly by 1 most of the time.
# For columns with gaps (diffs > 1) we apply a looser check: is the column sorted ascending
# and are >=90% of diffs positive?
SEQUENTIAL_THRESHOLD = 0.90


class SequenceDetectionProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]

        if col.dtype not in INTEGER_DTYPES:
            return findings

        non_null = col.drop_nulls()
        total = len(non_null)
        if total < 2:
            return findings

        # Compute consecutive differences
        diffs = non_null.diff().drop_nulls()
        n_diffs = len(diffs)
        if n_diffs == 0:
            return findings

        # A column is considered "sequential" when:
        #   - >=90% of diffs are exactly 1 (tight sequential), OR
        #   - >=90% of diffs are positive AND the values are sorted ascending
        #     (sequential with gaps — still clearly an ID/sequence column)
        unit_diffs = int((diffs == 1).sum())
        positive_diffs = int((diffs > 0).sum())
        sequential_ratio = unit_diffs / n_diffs
        positive_ratio = positive_diffs / n_diffs

        is_tight_sequential = sequential_ratio >= SEQUENTIAL_THRESHOLD
        is_sorted_sequential = (positive_ratio >= SEQUENTIAL_THRESHOLD) and non_null.is_sorted()

        if not (is_tight_sequential or is_sorted_sequential):
            # Not sequential — skip
            return findings

        # Column is sequential — find gaps
        col_min = int(non_null.min())
        col_max = int(non_null.max())
        expected_count = col_max - col_min + 1

        if expected_count <= total:
            # No gaps
            return findings

        # Find the actual gaps
        full_range = pl.Series("expected", range(col_min, col_max + 1))
        present = non_null.unique().sort()
        gaps = full_range.filter(~full_range.is_in(present))
        gap_count = len(gaps)

        sample_gaps = gaps.head(10).to_list()
        findings.append(Finding(
            severity=Severity.WARNING,
            column=column,
            check="sequence_detection",
            message=(
                f"Sequence gap detected in column '{column}': "
                f"{gap_count} missing value(s) in range [{col_min}, {col_max}]. "
                f"Gap positions (sample): {sample_gaps}"
            ),
            affected_rows=gap_count,
            sample_values=[str(v) for v in sample_gaps],
            suggestion="Investigate whether the missing sequence numbers indicate deleted or skipped records",
            confidence=0.7,
        ))

        return findings
