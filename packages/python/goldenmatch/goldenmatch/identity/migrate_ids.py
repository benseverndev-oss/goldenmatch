"""Migrate persisted identity record ids from the legacy ``{source}:hash:{12}``
scheme to the canonical ``{source}:h1:{12}`` scheme (SQLite + Postgres).

Non-breaking 1.x runway for the v2.0 removal of the legacy lookup candidate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from goldenmatch.core._hashing import record_fingerprint
from goldenmatch.identity.fingerprint_batch import _canonical_payload

# "{source}:hash:{12 hex}" -- source may itself contain ':' so match the SUFFIX.
_LEGACY_RE = re.compile(r"^(?P<source>.+):hash:[0-9a-f]{12}$")


@dataclass
class MigrationReport:
    scanned: int = 0
    rewritten: int = 0
    merged: int = 0
    clashed_distinct_entity: int = 0
    kept_unfingerprintable: int = 0
    edges_repointed: int = 0
    dry_run: bool = False


def _legacy_match(record_id: str) -> str | None:
    """Return the source prefix if ``record_id`` is a legacy :hash: id, else None."""
    m = _LEGACY_RE.match(record_id)
    return m.group("source") if m else None


def _recompute_h1_id(source: str, payload: dict[str, Any]) -> str | None:
    """Recompute the canonical :h1: id from a persisted payload. None if the
    canonical spec can't fingerprint it (mirrors resolve.py's legacy-only path)."""
    try:
        full = record_fingerprint(_canonical_payload(payload))
    except (TypeError, ValueError):
        return None
    return f"{source}:h1:{full[:12]}"


def _begin(store) -> None:
    if store._backend == "sqlite":
        store._conn.execute("BEGIN")

def _commit(store) -> None:
    if store._backend == "sqlite":
        store._conn.execute("COMMIT")

def _rollback(store) -> None:
    if store._backend == "sqlite":
        store._conn.execute("ROLLBACK")


def _count_edges_touching(store, rid: str) -> int:
    row = store._fetchone(
        "SELECT COUNT(*) AS n FROM evidence_edges "
        "WHERE record_a_id = ? OR record_b_id = ?", (rid, rid))
    return int(row["n"] if isinstance(row, dict) else row[0])


def _rename_record(store, old_id: str, new_id: str, source: str) -> int:
    """Rename a record_id everywhere (no clash). Returns edges touched."""
    touched = _count_edges_touching(store, old_id)
    new_pk = new_id[len(source) + 1:]
    store._exec("UPDATE source_records SET record_id = ?, source_pk = ? "
                "WHERE record_id = ?", (new_id, new_pk, old_id))
    store._exec("UPDATE evidence_edges SET record_a_id = ? WHERE record_a_id = ?",
                (new_id, old_id))
    store._exec("UPDATE evidence_edges SET record_b_id = ? WHERE record_b_id = ?",
                (new_id, old_id))
    # Re-canonicalize any pair touching new_id that is now (a > b).
    store._exec(
        "UPDATE evidence_edges SET record_a_id = record_b_id, record_b_id = record_a_id "
        "WHERE record_a_id > record_b_id AND (record_a_id = ? OR record_b_id = ?)",
        (new_id, new_id))
    return touched


def _do_migrate(store, *, dry_run: bool) -> MigrationReport:
    rpt = MigrationReport(dry_run=dry_run)
    rows = store._fetchall(
        "SELECT record_id, source, payload, entity_id FROM source_records", ())
    for row in rows:
        rid = row["record_id"]
        source = _legacy_match(rid)
        if source is None:
            continue
        rpt.scanned += 1
        payload = row["payload"]
        if isinstance(payload, str):       # sqlite stores TEXT; pg JSONB -> dict
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                rpt.kept_unfingerprintable += 1
                continue
        new_id = _recompute_h1_id(source, payload or {})
        if new_id is None:
            rpt.kept_unfingerprintable += 1
            continue
        # NOTE: clash handling is added in Task 3. For Task 2, assume no clash.
        if dry_run:
            rpt.rewritten += 1
            rpt.edges_repointed += _count_edges_touching(store, rid)
            continue
        rpt.edges_repointed += _rename_record(store, rid, new_id, source)
        rpt.rewritten += 1
    return rpt


def migrate_record_ids(store, *, dry_run: bool = False) -> MigrationReport:
    if store._backend == "mongo":
        raise NotImplementedError(
            "migrate-ids supports sqlite/postgres identity stores; for Mongo, "
            "re-ingest under the default :h1: scheme.")
    if dry_run:
        return _do_migrate(store, dry_run=True)
    if store._backend == "sqlite":
        _begin(store)
        try:
            rpt = _do_migrate(store, dry_run=False)
            _commit(store)
        except Exception:
            _rollback(store)
            raise
        return rpt
    # postgres
    with store._conn.transaction():
        return _do_migrate(store, dry_run=False)
