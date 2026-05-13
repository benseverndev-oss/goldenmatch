"""Web-UI label store.

Append-only JSONL at ``project_root/labels.jsonl``, last-write-wins on read
(deduped per canonical (row_id_a, row_id_b) pair).

This store is intentionally separate from:

- ``goldenmatch label`` CLI output — that writes a CSV consumed by
  ``goldenmatch evaluate`` / ``load_ground_truth_csv``. Different shape
  (CSV with ``label`` as 0/1), different consumer.
- ``MemoryStore`` corrections (Learning Memory v1.6.0) — those flow through
  ``add_correction()`` to drive ``apply_corrections`` and threshold learning.

If/when web labels need to feed evaluation or Learning Memory, an explicit
export step (JSONL → CSV, or a router-side ``add_correction`` call) is the
right path. Both are deferred for v1 — see the spec's Deferred section.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def _canonical_pair(row_id_a: int, row_id_b: int) -> tuple[int, int]:
    """Match the project-wide pair canonicalization (min, max).

    See the "Scored pairs are canonicalized" gotcha in the package CLAUDE.md.
    Without this, the inspector listing pair (1, 0) and the workbench
    relabeling pair (0, 1) would appear as separate entries.
    """
    a, b = int(row_id_a), int(row_id_b)
    return (a, b) if a <= b else (b, a)


def append_label(path: Path, entry: dict) -> dict:
    a, b = _canonical_pair(entry["row_id_a"], entry["row_id_b"])
    # Rebind to a fresh dict so the caller's payload isn't aliased.
    entry = {**entry, "row_id_a": a, "row_id_b": b, "ts": datetime.now(UTC).isoformat()}
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
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            # Tolerate a torn write at the tail of the file (single-writer
            # assumption holds, but a crashed-during-append leaves one bad
            # line). Skip rather than 500 the GET.
            continue
        key = _canonical_pair(rec["row_id_a"], rec["row_id_b"])
        # Keep the canonical key in the returned record too, so consumers
        # see the same shape regardless of input order.
        rec["row_id_a"], rec["row_id_b"] = key
        by_pair[key] = rec
    return list(by_pair.values())
