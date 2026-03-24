"""Scan history — append-only JSONL log of scan results."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile

logger = logging.getLogger(__name__)

__all__ = ["record_scan", "load_history", "get_previous_scan", "ScanRecord"]

HISTORY_DIR = Path(".goldencheck")
HISTORY_FILE = HISTORY_DIR / "history.jsonl"


@dataclass
class ScanRecord:
    timestamp: str
    file: str
    rows: int
    columns: int
    grade: str
    score: int
    errors: int
    warnings: int
    findings_count: int


def record_scan(
    file: str | Path,
    profile: DatasetProfile,
    findings: list[Finding],
) -> None:
    """Append a scan record to the history log."""
    errors = sum(1 for f in findings if f.severity == Severity.ERROR)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)

    by_col: dict[str, dict[str, int]] = {}
    for f in findings:
        if f.severity >= Severity.WARNING:
            by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
            key = "errors" if f.severity == Severity.ERROR else "warnings"
            by_col[f.column][key] = by_col[f.column].get(key, 0) + 1

    grade, score = profile.health_score(findings_by_column=by_col)

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": str(Path(file).name),
        "rows": profile.row_count,
        "columns": profile.column_count,
        "grade": grade,
        "score": score,
        "errors": errors,
        "warnings": warnings,
        "findings_count": len(findings),
    }

    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        logger.warning("Failed to write scan history: %s", e)


def load_history(
    file_filter: str | None = None,
    last_n: int | None = None,
) -> list[ScanRecord]:
    """Load scan records from history."""
    if not HISTORY_FILE.exists():
        return []

    records = []
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                record = ScanRecord(**data)
                if file_filter and record.file != file_filter:
                    continue
                records.append(record)
            except (json.JSONDecodeError, TypeError):
                continue

    if last_n:
        records = records[-last_n:]

    return records


def get_previous_scan(file: str | Path) -> ScanRecord | None:
    """Get the most recent scan record for a file."""
    name = str(Path(file).name)
    records = load_history(file_filter=name)
    return records[-1] if records else None
