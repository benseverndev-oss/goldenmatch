"""Finding model — represents a single validation finding."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

__all__ = ["Finding", "Severity", "CHECK_TYPES"]

class Severity(IntEnum):
    INFO = 1
    WARNING = 2
    ERROR = 3


# Canonical catalog of `Finding.check` labels. This is the single source of truth
# for the check-type vocabulary; keep it in sync with the `check="..."` literals in
# the profilers / relations / drift / denial / engine code (a scan test asserts the
# two agree, so a new check must be added here).
CHECK_TYPES: frozenset[str] = frozenset({
    # schema & type
    "existence", "required", "unmapped_column", "type_inference",
    # nullability
    "nullability", "null_correlation",
    # uniqueness & keys
    "unique", "uniqueness", "cardinality", "composite_key", "identity_safe_pk",
    "duplicate_rows", "near_duplicate_rows", "fuzzy_duplicate_values", "key_uniqueness_loss",
    # ranges & distribution
    "range", "range_distribution", "enum",
    # patterns & format
    "format_detection", "pattern_consistency", "encoding_detection", "sequence_detection",
    # temporal & freshness
    "temporal_order", "future_dated", "stale_data", "temporal_order_drift",
    # cross-column relations
    "cross_column", "cross_column_validation", "functional_dependency", "fd_violation",
    "correlation_break", "new_correlation",
    # referential integrity
    "referential_integrity",
    # denial constraints
    "denial_constraint",
    # drift
    "drift_detection", "distribution_drift", "entropy_drift", "bound_violation",
    "benford_drift", "type_drift", "pattern_drift", "new_pattern",
})

@dataclass
class Finding:
    severity: Severity
    column: str
    check: str
    message: str
    affected_rows: int = 0
    sample_values: list[str] = field(default_factory=list)
    suggestion: str | None = None
    pinned: bool = False
    source: str | None = None
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)

    def _repr_html_(self) -> str:
        colors = {Severity.ERROR: "#ff4444", Severity.WARNING: "#ffbb33", Severity.INFO: "#33b5e5"}
        labels = {Severity.ERROR: "ERROR", Severity.WARNING: "WARNING", Severity.INFO: "INFO"}
        color = colors.get(self.severity, "#888")
        label = labels.get(self.severity, "?")
        conf = "H" if self.confidence >= 0.8 else "M" if self.confidence >= 0.5 else "L"
        if self.source == "llm":
            source = ' <span style="color:#9b59b6;font-weight:bold">[LLM]</span>'
        elif self.source == "baseline_drift":
            source = ' <span style="color:#e67e22;font-weight:bold">[DRIFT]</span>'
        else:
            source = ""
        return (
            f'<div style="font-family:monospace;font-size:13px;padding:4px 8px;'
            f'border-left:3px solid {color};margin:2px 0">'
            f'<span style="color:{color};font-weight:bold">{label}</span> '
            f'<strong>{self.column}</strong> &middot; {self.check} &middot; '
            f'{self.message} '
            f'<span style="color:#888">({self.affected_rows} rows, {conf}{source})</span>'
            f'</div>'
        )
