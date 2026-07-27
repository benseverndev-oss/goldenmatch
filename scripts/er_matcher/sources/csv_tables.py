"""Shared CSV record-table reader used by the two-table PairSource loaders
(Leipzig, Magellan). CPU/box-safe: stdlib only (csv, pathlib)."""

from __future__ import annotations

import csv
from pathlib import Path


def read_id_table(root: Path, filename: str, prefix: str) -> dict[str, dict]:
    """Read a `id,<fields...>` record table, namespacing every id with
    `prefix` (e.g. `"A"`/`"B"`) so A-side and B-side ids never collide when
    merged into one shared entities/records dict for blocking, negative
    sampling, or split lookup."""
    entities: dict[str, dict] = {}
    with open(root / filename, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            native_id = row["id"]
            fields = {k: v for k, v in row.items() if k != "id"}
            entities[f"{prefix}:{native_id}"] = fields
    return entities
