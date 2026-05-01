from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TransformRecord:
    column: str
    transform: str
    affected_rows: int
    total_rows: int
    sample_before: list[str] = field(default_factory=list)
    sample_after: list[str] = field(default_factory=list)


@dataclass
class TransformError:
    column: str
    transform: str
    row: int
    error: str


@dataclass
class Manifest:
    source: str
    records: list[TransformRecord] = field(default_factory=list)
    errors: list[TransformError] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_record(self, record: TransformRecord) -> None:
        self.records.append(record)

    def add_error(self, column: str, transform: str, row: int, error: str) -> None:
        self.errors.append(TransformError(
            column=column, transform=transform, row=row, error=error
        ))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "created_at": self.created_at,
            "records": [
                {
                    "column": r.column,
                    "transform": r.transform,
                    "affected_rows": r.affected_rows,
                    "total_rows": r.total_rows,
                    "sample_before": r.sample_before,
                    "sample_after": r.sample_after,
                }
                for r in self.records
            ],
            "errors": [
                {
                    "column": e.column,
                    "transform": e.transform,
                    "row": e.row,
                    "error": e.error,
                }
                for e in self.errors
            ],
            "summary": {
                "total_transforms": len(self.records),
                "total_errors": len(self.errors),
                "columns_affected": list({r.column for r in self.records}),
            },
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))
