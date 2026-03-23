"""Finding model — represents a single validation finding."""
from __future__ import annotations
from enum import IntEnum
from dataclasses import dataclass, field

class Severity(IntEnum):
    INFO = 1
    WARNING = 2
    ERROR = 3

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

    def _repr_html_(self) -> str:
        colors = {Severity.ERROR: "#ff4444", Severity.WARNING: "#ffbb33", Severity.INFO: "#33b5e5"}
        labels = {Severity.ERROR: "ERROR", Severity.WARNING: "WARNING", Severity.INFO: "INFO"}
        color = colors.get(self.severity, "#888")
        label = labels.get(self.severity, "?")
        conf = "H" if self.confidence >= 0.8 else "M" if self.confidence >= 0.5 else "L"
        source = " [LLM]" if self.source == "llm" else ""
        return (
            f'<div style="font-family:monospace;font-size:13px;padding:4px 8px;'
            f'border-left:3px solid {color};margin:2px 0">'
            f'<span style="color:{color};font-weight:bold">{label}</span> '
            f'<strong>{self.column}</strong> &middot; {self.check} &middot; '
            f'{self.message} '
            f'<span style="color:#888">({self.affected_rows} rows, {conf}{source})</span>'
            f'</div>'
        )
