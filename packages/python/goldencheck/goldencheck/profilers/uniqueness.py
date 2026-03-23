"""Uniqueness profiler — detects primary key candidates and duplicates."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

class UniquenessProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        total = len(col)
        non_null = col.drop_nulls()
        unique_count = non_null.n_unique() if len(non_null) > 0 else 0
        unique_pct = unique_count / len(non_null) if len(non_null) > 0 else 0
        if unique_pct == 1.0 and total >= 10:
            # 100% unique, 100+ rows → 0.95; fewer → lower confidence
            confidence = 0.95 if total >= 100 else 0.7
            findings.append(Finding(severity=Severity.INFO, column=column, check="uniqueness",
                message=f"100% unique across {total} rows — likely primary key",
                confidence=confidence))
        elif unique_pct < 1.0:
            dup_count = len(non_null) - unique_count
            if dup_count > 0 and unique_pct > 0.95:
                # 95-99% unique → confidence 0.6
                findings.append(Finding(severity=Severity.WARNING, column=column, check="uniqueness",
                    message=f"Near-unique column ({unique_pct:.1%} unique) with {dup_count} duplicates",
                    affected_rows=dup_count,
                    confidence=0.6))
        return findings
