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
                # >95% match → 0.9; 70-95% → 0.6
                detect_confidence = 0.9 if match_pct > 0.95 else 0.6
                findings.append(Finding(
                    severity=Severity.INFO,
                    column=column,
                    check="format_detection",
                    message=f"Column appears to contain {fmt_name} values ({match_pct:.1%} match)",
                    affected_rows=match_count,
                    confidence=detect_confidence,
                ))
                non_match_count = total - match_count
                if non_match_count > 0:
                    non_matching = non_null.filter(~matches)
                    sample = non_matching.head(5).to_list()
                    # Non-matching findings inherit same confidence as detection
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
                        confidence=detect_confidence,
                    ))

                # Cross-format detection: check if non-matching values belong to a different format
                CROSS_FORMAT_CHECKS = {
                    "url": [("email", EMAIL_REGEX)],
                    "email": [("url", URL_PREFIX)],
                    "phone": [("email", EMAIL_REGEX)],
                }
                for other_fmt, other_pattern in CROSS_FORMAT_CHECKS.get(fmt_name, []):
                    if non_match_count > 0:
                        wrong_fmt_matches = non_null.filter(~matches).str.contains(other_pattern)
                        wrong_fmt_count = wrong_fmt_matches.sum()
                        if wrong_fmt_count > 0:
                            wrong_pct = wrong_fmt_count / total
                            findings.append(Finding(
                                severity=Severity.ERROR,
                                column=column,
                                check="format_detection",
                                message=(
                                    f"Column is detected as {fmt_name} but {wrong_fmt_count} value(s) "
                                    f"({wrong_pct:.1%}) appear to be {other_fmt} — wrong type values present"
                                ),
                                affected_rows=wrong_fmt_count,
                                suggestion=f"Remove or correct {other_fmt} values from this {fmt_name} column",
                                confidence=detect_confidence,
                            ))

        return findings
