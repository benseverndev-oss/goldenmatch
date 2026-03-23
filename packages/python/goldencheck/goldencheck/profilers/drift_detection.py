"""Drift detection profiler — detects distribution drift between first and second half of data."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

NUMERIC_DTYPES = (
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Float32, pl.Float64,
)

# Minimum row count to attempt drift detection (small samples are unreliable)
MIN_ROWS = 1000

# Number of standard deviations between means to flag as drift
DRIFT_STDDEV_THRESHOLD = 2.0


class DriftDetectionProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        total = len(col)

        if total < MIN_ROWS:
            return findings

        mid = total // 2
        first_half = col[:mid].drop_nulls()
        second_half = col[mid:].drop_nulls()

        if len(first_half) == 0 or len(second_half) == 0:
            return findings

        is_numeric = col.dtype in NUMERIC_DTYPES

        if is_numeric:
            # Numeric drift: compare means
            mean1 = first_half.mean()
            mean2 = second_half.mean()
            std1 = first_half.std()

            if mean1 is None or mean2 is None or std1 is None or std1 == 0:
                return findings

            deviation = abs(mean2 - mean1) / std1
            if deviation > DRIFT_STDDEV_THRESHOLD:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=column,
                    check="drift_detection",
                    message=(
                        f"Distribution shift detected in '{column}': "
                        f"mean changed from {mean1:.4g} (first half) to {mean2:.4g} (second half), "
                        f"a shift of {deviation:.1f} standard deviations — possible temporal drift"
                    ),
                    affected_rows=len(second_half),
                    suggestion="Investigate whether the data order is temporal and whether the shift is expected",
                    confidence=0.6,
                ))
        else:
            # Categorical drift: look for new categories in second half
            cats_first = set(first_half.cast(pl.String).to_list())
            cats_second = set(second_half.cast(pl.String).to_list())
            new_cats = cats_second - cats_first

            if new_cats:
                sample_new = sorted(new_cats)[:10]
                # Count rows in second half that contain new categories
                new_cat_mask = second_half.cast(pl.String).is_in(list(new_cats))
                affected = int(new_cat_mask.sum())
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=column,
                    check="drift_detection",
                    message=(
                        f"Categorical drift detected in '{column}': "
                        f"{len(new_cats)} new categor(y/ies) appear in the second half of the data "
                        f"that are absent from the first half: {sample_new}"
                    ),
                    affected_rows=affected,
                    sample_values=[str(v) for v in sample_new],
                    suggestion="Verify whether new categories are expected or indicate schema/labelling drift",
                    confidence=0.6,
                ))

        return findings
