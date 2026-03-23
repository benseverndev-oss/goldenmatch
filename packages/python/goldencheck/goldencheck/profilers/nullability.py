"""Nullability profiler — detects required vs. optional columns."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

class NullabilityProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        total = len(col)
        null_count = col.null_count()
        null_pct = null_count / total if total > 0 else 0
        if null_count == total:
            findings.append(Finding(severity=Severity.ERROR, column=column, check="nullability",
                message=f"Column is entirely null ({total} rows)", affected_rows=total,
                confidence=0.99))
        elif null_count == 0 and total >= 10:
            # 0 nulls in 1000+ rows → 0.95; 0 nulls <50 → 0.5; else 0.7
            if total >= 1000:
                confidence = 0.95
            elif total < 50:
                confidence = 0.5
            else:
                confidence = 0.7
            findings.append(Finding(severity=Severity.INFO, column=column, check="nullability",
                message=f"0 nulls across {total} rows — likely required",
                confidence=confidence))
        elif null_pct > 0 and null_pct < 1:
            findings.append(Finding(severity=Severity.INFO, column=column, check="nullability",
                message=f"{null_count} nulls ({null_pct:.1%}) — column is optional", affected_rows=null_count,
                confidence=0.7))
        return findings
