"""Encoding detection profiler — detects encoding anomalies in string columns."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

# Zero-width Unicode characters
ZERO_WIDTH_CHARS = "\u200B\u200C\u200D\uFEFF"

# Smart/curly quotes
SMART_QUOTES = "\u2018\u2019\u201C\u201D"

# Non-ASCII Latin-1 supplement range (U+0080–U+00FF)
# Common encoding-issue chars: é, ñ, ü, etc.
NON_ASCII_PATTERN = r"[^\x00-\x7F]"

# Zero-width pattern
ZERO_WIDTH_PATTERN = r"[\u200B\u200C\u200D\uFEFF]"

# Smart quotes pattern
SMART_QUOTES_PATTERN = r"[\u2018\u2019\u201C\u201D]"

# Control characters (non-printable, excluding tab/newline/CR)
CONTROL_CHAR_PATTERN = r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"


class EncodingDetectionProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]

        if col.dtype not in (pl.Utf8, pl.String):
            return findings

        non_null = col.drop_nulls()
        total = len(non_null)
        if total == 0:
            return findings

        # 1. Zero-width Unicode characters (confidence: 0.8 — almost always wrong)
        zw_mask = non_null.str.contains(ZERO_WIDTH_PATTERN)
        zw_count = zw_mask.sum()
        if zw_count > 0:
            sample = non_null.filter(zw_mask).head(5).to_list()
            findings.append(Finding(
                severity=Severity.WARNING,
                column=column,
                check="encoding_detection",
                message=(
                    f"{zw_count} value(s) contain zero-width unicode characters "
                    f"(U+200B/U+200C/U+200D/U+FEFF) — likely encoding artifact"
                ),
                affected_rows=zw_count,
                sample_values=[repr(v) for v in sample],
                suggestion="Strip zero-width characters from this column",
                confidence=0.8,
            ))

        # 2. Smart/curly quotes (confidence: 0.6 — could be intentional)
        sq_mask = non_null.str.contains(SMART_QUOTES_PATTERN)
        sq_count = sq_mask.sum()
        if sq_count > 0:
            sample = non_null.filter(sq_mask).head(5).to_list()
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="encoding_detection",
                message=(
                    f"{sq_count} value(s) contain smart quote / curly quote characters "
                    f"(\u2018\u2019\u201C\u201D) — may be encoding inconsistency"
                ),
                affected_rows=sq_count,
                sample_values=[repr(v) for v in sample],
                suggestion="Normalise smart quotes to straight quotes if encoding consistency is required",
                confidence=0.6,
            ))

        # 3. Non-ASCII characters (confidence: 0.5 — could be valid international text)
        na_mask = non_null.str.contains(NON_ASCII_PATTERN)
        na_count = na_mask.sum()
        if na_count > 0:
            # Only flag if zero-width chars were NOT already found for the same rows
            # (avoid duplicate noise), but still report non-ASCII separately
            sample = non_null.filter(na_mask).head(5).to_list()
            findings.append(Finding(
                severity=Severity.INFO,
                column=column,
                check="encoding_detection",
                message=(
                    f"{na_count} value(s) contain non-ASCII / unicode characters — "
                    f"verify encoding is intentional (international text vs. mojibake)"
                ),
                affected_rows=na_count,
                sample_values=[repr(v) for v in sample],
                suggestion="Confirm the source encoding; if mojibake, re-encode from Latin-1 to UTF-8",
                confidence=0.5,
            ))

        # 4. Control characters (confidence: 0.8 — rarely valid in data)
        ctrl_mask = non_null.str.contains(CONTROL_CHAR_PATTERN)
        ctrl_count = ctrl_mask.sum()
        if ctrl_count > 0:
            sample = non_null.filter(ctrl_mask).head(5).to_list()
            findings.append(Finding(
                severity=Severity.WARNING,
                column=column,
                check="encoding_detection",
                message=(
                    f"{ctrl_count} value(s) contain non-printable control characters — "
                    f"likely encoding or data extraction issue"
                ),
                affected_rows=ctrl_count,
                sample_values=[repr(v) for v in sample],
                suggestion="Strip or replace control characters",
                confidence=0.8,
            ))

        return findings
