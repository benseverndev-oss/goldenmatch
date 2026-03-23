"""Pattern consistency profiler — detects inconsistent string patterns within a column."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

MINORITY_THRESHOLD = 0.30


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

        for i in range(1, n_patterns):
            minority_pattern = pattern_counts[column][i]
            minority_count = int(pattern_counts["count"][i])
            minority_pct = minority_count / total

            if minority_pct < MINORITY_THRESHOLD:
                # Find sample values that match this minority pattern
                mask = patterns == minority_pattern
                sample_vals = non_null.filter(mask).head(5).to_list()
                findings.append(Finding(
                    severity=Severity.WARNING,
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
                ))

        return findings
