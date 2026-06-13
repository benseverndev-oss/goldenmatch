"""Migrate persisted identity record ids from the legacy ``{source}:hash:{12}``
scheme to the canonical ``{source}:h1:{12}`` scheme (SQLite + Postgres).

Non-breaking 1.x runway for the v2.0 removal of the legacy lookup candidate.
"""
from __future__ import annotations

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
