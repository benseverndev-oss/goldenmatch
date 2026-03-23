"""Pattern consistency profiler — detects inconsistent string patterns within a column."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

MINORITY_THRESHOLD = 0.30  # only flag patterns below this threshold
WARNING_THRESHOLD = 0.05  # <5% → WARNING (very rare, likely error); 5-30% → INFO


def _generalize(value: str) -> str:
    """Replace digits with D and letters with L, keeping punctuation as-is."""
    result = []
    for ch in value:
        if ch.isdigit():
            result.append("D")
        elif ch.isalpha():
            result.append("L")
        else:
            result.append(ch)
    return "".join(result)


class PatternConsistencyProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]

        if col.dtype not in (pl.Utf8, pl.String):
            return findings

        non_null = col.drop_nulls()
        total = len(non_null)
        if total == 0:
            return findings

        # Build pattern counts using Python (Polars map_elements for UDF)
        patterns = non_null.map_elements(_generalize, return_dtype=pl.String)
        pattern_counts = (
            patterns.value_counts()
            .sort("count", descending=True)
        )

        n_patterns = len(pattern_counts)
        if n_patterns <= 1:
            # All values share the same pattern — no inconsistency
            return findings

        dominant_count = pattern_counts["count"][0]
        dominant_pattern = pattern_counts[column][0]

        # Collect all minority patterns (rarest first — already sorted ascending by reversing)
        minority_candidates = []
        for i in range(1, n_patterns):
            minority_pattern = pattern_counts[column][i]
            minority_count = int(pattern_counts["count"][i])
            minority_pct = minority_count / total

            if minority_pct < MINORITY_THRESHOLD:
                minority_candidates.append((minority_pattern, minority_count, minority_pct))

        if not minority_candidates:
            return findings

        # Sort rarest first (most likely errors)
        minority_candidates.sort(key=lambda x: x[1])

        # Cap at top 5
        MAX_PATTERNS = 5
        total_minority = len(minority_candidates)
        emitted = minority_candidates[:MAX_PATTERNS]

        for minority_pattern, minority_count, minority_pct in emitted:
            # <5% → WARNING (very rare, likely error); 5-30% → INFO (valid variant)
            if minority_pct < WARNING_THRESHOLD:
                severity = Severity.WARNING
                confidence = 0.8
            else:
                severity = Severity.INFO
                confidence = 0.5
            # Find sample values that match this minority pattern
            mask = patterns == minority_pattern
            sample_vals = non_null.filter(mask).head(5).to_list()
            findings.append(Finding(
                severity=severity,
                column=column,
                check="pattern_consistency",
                message=(
                    f"Inconsistent pattern detected: '{minority_pattern}' appears in "
                    f"{minority_count} row(s) ({minority_pct:.1%}) vs dominant pattern "
                    f"'{dominant_pattern}' ({dominant_count} row(s))"
                ),
                affected_rows=minority_count,
                sample_values=[str(v) for v in sample_vals],
                suggestion="Standardize values to a single format/pattern",
                confidence=confidence,
            ))

        # Summary finding if more than MAX_PATTERNS minority patterns exist
        if total_minority > MAX_PATTERNS:
            extra = total_minority - MAX_PATTERNS
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="pattern_consistency",
                message=(
                    f"{extra} additional inconsistent pattern(s) detected (showing top {MAX_PATTERNS})"
                ),
                suggestion="Standardize values to a single format/pattern",
                confidence=0.5,
            ))

        return findings
