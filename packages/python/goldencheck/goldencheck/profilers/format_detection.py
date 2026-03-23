"""Format detection profiler — detects email, phone, and URL patterns in string columns."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

EMAIL_REGEX = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
PHONE_REGEX = r"^\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$"
URL_PREFIX = r"^https?://"

FORMATS = [
    ("email", EMAIL_REGEX),
    ("phone", PHONE_REGEX),
    ("url", URL_PREFIX),
]


class FormatDetectionProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]

        if col.dtype not in (pl.Utf8, pl.String):
            return findings

        non_null = col.drop_nulls()
        total = len(non_null)
        if total == 0:
            return findings

        for fmt_name, pattern in FORMATS:
            matches = non_null.str.contains(pattern)
            match_count = matches.sum()
            match_pct = match_count / total

            if match_pct > 0.70:
                findings.append(Finding(
                    severity=Severity.INFO,
                    column=column,
                    check="format_detection",
                    message=f"Column appears to contain {fmt_name} values ({match_pct:.1%} match)",
                    affected_rows=match_count,
                ))
                non_match_count = total - match_count
                if non_match_count > 0:
                    non_matching = non_null.filter(~matches)
                    sample = non_matching.head(5).to_list()
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        column=column,
                        check="format_detection",
                        message=(
                            f"{non_match_count} value(s) do not match expected {fmt_name} format"
                        ),
                        affected_rows=non_match_count,
                        sample_values=[str(v) for v in sample],
                        suggestion=f"Review non-{fmt_name} values for data quality issues",
                    ))

        return findings
