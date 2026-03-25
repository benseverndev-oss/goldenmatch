"""Track transform runs for comparison over time."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path

HISTORY_DIR = Path.home() / ".goldenflow" / "history"


@dataclass
class RunRecord:
    run_id: str
    source: str
    timestamp: str
    rows: int
    columns: int
    transforms_applied: int
    errors: int
    duration_seconds: float
    config_hash: str = ""
    manifest_path: str = ""


def save_run(record: RunRecord) -> Path:
    """Save a run record to history."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{record.run_id}.json"
    path.write_text(json.dumps(asdict(record), indent=2))
    return path


def list_runs(limit: int = 20) -> list[RunRecord]:
    """List recent run records, newest first."""
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    runs = []
    for f in files[:limit]:
        data = json.loads(f.read_text())
        runs.append(RunRecord(**data))
    return runs


def get_run(run_id: str) -> RunRecord | None:
    """Get a specific run record."""
    path = HISTORY_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return RunRecord(**json.loads(path.read_text()))


def generate_run_id() -> str:
    """Generate a unique run ID."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + f"{int(time.time() * 1000) % 10000:04d}"
