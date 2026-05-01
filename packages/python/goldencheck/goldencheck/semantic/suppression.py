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
                # For pattern_consistency findings on geo/identifier columns,
                # skip suppression if patterns are code-like (mostly digits)
                # and differ significantly in length (e.g. 5-digit vs 9-digit
                # zip codes). This preserves real format inconsistencies while
                # still suppressing natural variation in names/cities.
                if (
                    f.check == "pattern_consistency"
                    and classification.type_name in ("geo", "identifier")
                ):
                    dom = f.metadata.get("dominant_pattern")
                    minor = f.metadata.get("minority_pattern")
                    if dom and minor and abs(len(dom) - len(minor)) > 1:
                        # Only skip suppression when BOTH patterns are
                        # code-like (mostly digits). Mixed patterns (e.g.
                        # DD vs LLLLLLL in age columns) should stay suppressed.
                        dom_digit = sum(1 for c in dom if c == "D") / max(len(dom), 1)
                        minor_digit = sum(1 for c in minor if c == "D") / max(len(minor), 1)
                        if dom_digit > 0.5 and minor_digit > 0.5:
                            result.append(f)
                            continue
                result.append(replace(
                    f,
                    severity=Severity.INFO,
                    message=f"{f.message} (suppressed: {classification.type_name} column)",
                ))
                continue

        result.append(f)
    return result
