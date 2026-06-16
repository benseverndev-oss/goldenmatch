"""Expand the seed entities into a flat, labelled record table.

Reads ``seeds.jsonl`` (one ground-truth entity per line, with a list of
surface-form *mentions*) and writes ``records.csv`` -- one row per mention.

The hidden ground-truth columns are ``entity_id`` and ``failure_class``;
adapters under test only ever see ``mention`` (and, for multi-field
configurations, ``entity_type`` / ``context``). Two records are a true
*match* iff they share an ``entity_id``.

Crucially, the ``same_name_collision`` and ``temporal_version`` classes
contain DISTINCT entities whose surface forms collide ("First National Bank"
in Algeria vs the USA; "BTC Halving 2020" vs "2024"). They are negative
tests: a string-only resolver over-merges them (false positives), which is
exactly where naive dedup loses precision. See ``../TAXONOMY.md``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).parent
SEEDS = HERE / "seeds.jsonl"
RECORDS = HERE / "records.csv"

FIELDNAMES = [
    "record_id",
    "mention",
    "entity_type",
    "context",
    "entity_id",
    "failure_class",
]


def build_rows() -> list[dict]:
    rows: list[dict] = []
    rid = 0
    with SEEDS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            ent = json.loads(line)
            for mention in ent["mentions"]:
                rows.append(
                    {
                        "record_id": rid,
                        "mention": mention,
                        "entity_type": ent["type"],
                        "context": ent.get("context", ""),
                        "entity_id": ent["entity_id"],
                        "failure_class": ent["failure_class"],
                    }
                )
                rid += 1
    return rows


def write_csv(rows: list[dict], path: Path = RECORDS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    rows = build_rows()
    write_csv(rows)
    n_entities = len({r["entity_id"] for r in rows})
    n_classes = len({r["failure_class"] for r in rows})
    print(
        f"Wrote {len(rows)} records / {n_entities} entities / "
        f"{n_classes} failure classes -> {RECORDS}"
    )


if __name__ == "__main__":
    main()
