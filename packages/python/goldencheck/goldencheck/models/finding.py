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
