from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def append_label(path: Path, entry: dict) -> dict:
    entry = {**entry, "ts": datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_labels_dedup(path: Path) -> list[dict]:
    if not path.exists():
        return []
    by_pair: dict[tuple[int, int], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        by_pair[(rec["row_id_a"], rec["row_id_b"])] = rec
    return list(by_pair.values())
