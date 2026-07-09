"""Type inference profiler — detects mixed types and type mismatches."""
from __future__ import annotations

from goldencheck.core.frame import to_frame
from goldencheck.models.finding import Finding, Severity
from goldencheck.profilers.base import BaseProfiler


class TypeInferenceProfiler(BaseProfiler):
    def profile(self, frame, column: str, *, context: dict | None = None) -> list[Finding]:
        frame = to_frame(frame)
        findings: list[Finding] = []
        col = frame.column(column)
        dt = col.dtype
        if dt == "str":
            non_null = col.drop_nulls()
            if len(non_null) == 0:
                return findings
            cast_result = non_null.cast("float", strict=False)
            numeric_count = len(non_null) - cast_result.null_count()
            numeric_pct = numeric_count / len(non_null) if len(non_null) > 0 else 0
            if numeric_pct >= 0.8:
                int_cast = non_null.cast("int", strict=False)
                int_count = len(non_null) - int_cast.null_count()
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

        # Check: integer/float column that should be string based on name
        SHOULD_BE_STRING = ["zip", "postal", "phone", "fax", "ssn", "npi", "id", "code", "sku"]
        if dt in ("int", "float"):
            col_lower = column.lower()
            for hint in SHOULD_BE_STRING:
                if hint in col_lower:
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        column=column,
                        check="type_inference",
                        message=f"Column '{column}' is numeric but name suggests it should be string (may lose leading zeros)",
                        confidence=0.6,
                        suggestion="Consider storing as string to preserve formatting",
                    ))
                    break

        return findings
