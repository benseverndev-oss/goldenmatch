"""Suppression engine — downgrades irrelevant findings based on column semantic type."""
from __future__ import annotations
from dataclasses import replace
from goldencheck.models.finding import Finding, Severity
from goldencheck.semantic.classifier import ColumnClassification, TypeDef

def apply_suppression(
    findings: list[Finding],
    column_types: dict[str, ColumnClassification],
    type_defs: dict[str, TypeDef],
) -> list[Finding]:
    """Downgrade findings where check is in the column type's suppress list."""
    result = []
    for f in findings:
        # Only suppress WARNING/ERROR
        if f.severity not in (Severity.WARNING, Severity.ERROR):
            result.append(f)
            continue
        # Never suppress LLM findings
        if f.source == "llm":
            result.append(f)
            continue
        # Never suppress high initial confidence (>= 0.9)
        if f.confidence >= 0.9:
            result.append(f)
            continue

        # Check if this finding should be suppressed
        classification = column_types.get(f.column)
        if classification and classification.type_name:
            type_def = type_defs.get(classification.type_name)
            if type_def and f.check in type_def.suppress:
                result.append(replace(
                    f,
                    severity=Severity.INFO,
                    message=f"{f.message} (suppressed: {classification.type_name} column)",
                ))
                continue

        result.append(f)
    return result
