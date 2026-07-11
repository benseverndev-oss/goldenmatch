"""Range and distribution profiler — detects outliers and reports min/max for numeric columns."""
from __future__ import annotations

import logging

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

logger = logging.getLogger(__name__)


class RangeDistributionProfiler(BaseProfiler):
    def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:
        frame = to_frame(frame)
        findings: list[Finding] = []
        col = frame.column(column)
        dtype = col.dtype
        is_numeric = dtype in ("int", "uint", "float")

        # Chain: if type inference flagged as mostly numeric, cast and run
        if not is_numeric and context and context.get(column, {}).get("mostly_numeric"):
            col = col.cast("float", strict=False).drop_nulls()
            is_numeric = True
        elif not is_numeric:
            return findings

        non_null = col.drop_nulls() if is_numeric and dtype in ("int", "uint", "float") else col
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

        # Shadow-compute the fused native numeric_stats kernel on the real scan
        # path so it runs against production shapes ahead of the Flip (see
        # tests/engine/test_w2_shadow.py for the parity assertion). NOT
        # authoritative -- the Polars-computed mean/std/min/max above stay the
        # emitted values. Fully guarded + swallow-on-error: shadow only.
        native_stats = native_enabled("numeric_stats")
        if native_stats:
            try:
                native_module().column_numeric_stats(non_null.to_arrow())
            except Exception as e:  # noqa: BLE001 - shadow-only, never affects output
                logger.debug("column_numeric_stats shadow failed on %s: %s", column, e)

        if std is not None and std > 0:
            lower = mean - 3 * std
            upper = mean + 3 * std
            outliers = non_null.filter_outside(lower, upper)
            outlier_count = len(outliers)
            # Shadow the outlier count/sample kernel with the POLARS-computed
            # lower/upper (spec B1) so boundary values agree with filter_outside.
            if native_stats:
                try:
                    native_module().count_outside(non_null.to_arrow(), lower, upper)
                except Exception as e:  # noqa: BLE001 - shadow-only, never affects output
                    logger.debug("count_outside shadow failed on %s: %s", column, e)
            if outlier_count > 0:
                sample = outliers.to_list()[:5]
                # Determine how many stddevs outliers are
                # Use max deviation to determine confidence
                max_dev = max(
                    abs(float(non_null.max()) - mean) / std,
                    abs(float(non_null.min()) - mean) / std,
                ) if std > 0 else 0
                confidence = 0.9 if max_dev > 5 else 0.7
                findings.append(Finding(
                    severity=Severity.WARNING,
                    column=column,
                    check="range_distribution",
                    message=f"{outlier_count} outlier(s) detected beyond 3 standard deviations",
                    affected_rows=outlier_count,
                    sample_values=[str(v) for v in sample],
                    suggestion="Investigate outlier values for data entry errors or anomalies",
                    confidence=confidence,
                ))

        return findings
