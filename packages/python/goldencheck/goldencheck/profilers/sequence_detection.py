"""Sequence gap detection profiler — detects gaps in sequential integer columns."""
from __future__ import annotations

import logging

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

logger = logging.getLogger(__name__)

# Minimum fraction of consecutive diffs == 1 to consider column sequential.
# We use this threshold on columns where the values increment exactly by 1 most of the time.
# For columns with gaps (diffs > 1) we apply a looser check: is the column sorted ascending
# and are >=90% of diffs positive?
SEQUENTIAL_THRESHOLD = 0.90


class SequenceDetectionProfiler(BaseProfiler):
    def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:
        frame = to_frame(frame)
        findings: list[Finding] = []
        col = frame.column(column)

        if col.dtype not in ("int", "uint"):
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
        unit_diffs = diffs.count_eq(1)
        positive_diffs = diffs.count_gt(0)
        sequential_ratio = unit_diffs / n_diffs
        positive_ratio = positive_diffs / n_diffs

        is_tight_sequential = sequential_ratio >= SEQUENTIAL_THRESHOLD
        is_sorted_sequential = (positive_ratio >= SEQUENTIAL_THRESHOLD) and non_null.is_sorted()

        if not (is_tight_sequential or is_sorted_sequential):
            # Not sequential — skip
            return findings

        # Shadow-compute the fused native sequence_analysis kernel on the real
        # scan path so it runs against production shapes ahead of the Flip (see
        # tests/engine/test_w2_shadow.py for the parity assertion). NOT
        # authoritative -- the Polars gap scan below stays the emitted values.
        # Fully guarded + swallow-on-error: shadow only.
        if native_enabled("sequence_analysis"):
            try:
                native_module().sequence_analysis(non_null.to_arrow())
            except Exception as e:  # noqa: BLE001 - shadow-only, never affects output
                logger.debug("sequence_analysis shadow failed on %s: %s", column, e)

        # Column is sequential — find gaps
        col_min = int(non_null.min())
        col_max = int(non_null.max())
        expected_count = col_max - col_min + 1

        if expected_count <= total:
            # No gaps
            return findings

        # Find the actual gaps
        present = set(non_null.unique().to_list())
        gaps = [v for v in range(col_min, col_max + 1) if v not in present]
        gap_count = len(gaps)

        sample_gaps = gaps[:10]
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
