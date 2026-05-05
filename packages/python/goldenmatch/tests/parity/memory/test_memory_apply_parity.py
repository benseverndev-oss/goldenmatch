"""Cross-language apply-outcome parity for Learning Memory.

Loads the shared ``memory_corrections.json`` and ``memory_apply_inputs.json``
fixtures, seeds an in-process Python ``MemoryStore`` with the corrections,
runs the canonical ``apply_corrections`` against the input scored pairs / df
/ matchkey fields, and asserts the resulting ``(adjusted, stats)`` matches
the expected JSON byte-for-byte (after sorting ``stale_pairs``).

This is the load-bearing parity check: if the algorithm drifts in either
language a fixture diff will trip the test.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from goldenmatch.core.memory.corrections import apply_corrections
from goldenmatch.core.memory.store import Correction, MemoryStore

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CORRECTIONS_PATH = FIXTURE_DIR / "memory_corrections.json"
APPLY_PATH = FIXTURE_DIR / "memory_apply_inputs.json"


def _entry_to_correction(entry: dict) -> Correction:
    ts = entry["created_at"]
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return Correction(
        id=entry["id"],
        id_a=entry["id_a"], id_b=entry["id_b"],
        decision=entry["decision"], source=entry["source"],
        trust=entry["trust"],
        field_hash=entry["field_hash"], record_hash=entry["record_hash"],
        original_score=entry["original_score"],
        matchkey_name=entry["matchkey_name"],
        reason=entry["reason"], dataset=entry["dataset"],
        created_at=datetime.fromisoformat(ts).astimezone(timezone.utc),
    )


@pytest.fixture
def seeded_store(tmp_path) -> MemoryStore:
    entries = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "memory.db"))
    for e in entries:
        store.add_correction(_entry_to_correction(e))
    yield store
    store.close()


@pytest.fixture(scope="module")
def apply_inputs() -> dict:
    return json.loads(APPLY_PATH.read_text(encoding="utf-8"))


def test_apply_reproduces_expected_outcome(seeded_store, apply_inputs):
    df = pl.DataFrame(apply_inputs["df"])
    scored = [tuple(p) for p in apply_inputs["scored_pairs"]]
    adjusted, stats = apply_corrections(
        scored, seeded_store, df, apply_inputs["matchkey_fields"],
        dataset=apply_inputs["dataset"],
        reanchor=apply_inputs["reanchor"],
    )

    expected = apply_inputs["expected"]
    expected_adjusted = [tuple(p) for p in expected["adjusted"]]
    assert adjusted == expected_adjusted

    assert stats.applied == expected["stats"]["applied"]
    assert stats.stale == expected["stats"]["stale"]
    assert stats.stale_ambiguous == expected["stats"]["stale_ambiguous"]
    assert stats.stale_unanchorable == expected["stats"]["stale_unanchorable"]
    assert stats.total_pairs == expected["stats"]["total_pairs"]

    got_sorted = sorted(stats.stale_pairs, key=lambda p: (p[0], p[1]))
    want_sorted = sorted(
        [tuple(p) for p in expected["stats"]["stale_pairs"]],
        key=lambda p: (p[0], p[1]),
    )
    assert got_sorted == want_sorted
