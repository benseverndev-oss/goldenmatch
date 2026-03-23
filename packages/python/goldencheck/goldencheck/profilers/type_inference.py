"""Type inference profiler — detects mixed types and type mismatches."""
from __future__ import annotations
import polars as pl
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler

class TypeInferenceProfiler(BaseProfiler):
    def profile(self, df: pl.DataFrame, column: str, *, context: dict | None = None) -> list[Finding]:
        findings: list[Finding] = []
        col = df[column]
        dtype = col.dtype
        if dtype == pl.Utf8 or dtype == pl.String:
            non_null = col.drop_nulls()
            if len(non_null) == 0:
                return findings
            cast_result = non_null.cast(pl.Float64, strict=False)
            numeric_count = cast_result.is_not_null().sum()
            numeric_pct = numeric_count / len(non_null) if len(non_null) > 0 else 0
            if numeric_pct >= 0.8:
                int_cast = non_null.cast(pl.Int64, strict=False)
                int_count = int_cast.is_not_null().sum()
                int_pct = int_count / len(non_null)
                type_name = "integer" if int_pct > 0.9 else "numeric"
                non_numeric = len(non_null) - numeric_count
                # Write context so other profilers can chain
                if context is not None:
                    context.setdefault(column, {})["mostly_numeric"] = True
                findings.append(Finding(
                    severity=Severity.WARNING, column=column, check="type_inference",
                    message=f"Column is string but {numeric_pct:.0%} of values are {type_name} ({non_numeric} non-{type_name} values)",
                    affected_rows=non_numeric,
                    suggestion=f"Consider casting to {type_name}",
                    confidence=0.9,
                ))
            elif numeric_pct > 0 and numeric_pct < 0.05:
                # Minority numeric values in a mostly-text column — suspicious but low confidence
                minority_count = numeric_count
                findings.append(Finding(
                    severity=Severity.INFO, column=column, check="type_inference",
                    message=f"Column is string but {numeric_pct:.1%} of values appear numeric ({minority_count} values) — possible data entry error",
                    affected_rows=minority_count,
                    suggestion="Investigate numeric values in this text column",
                    confidence=0.3,
                ))
        return findings
