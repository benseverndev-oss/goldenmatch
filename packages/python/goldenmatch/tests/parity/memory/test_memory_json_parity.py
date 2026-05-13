"""Cross-language JSON wire-format parity for Learning Memory.

Loads the shared ``memory_corrections.json`` fixture and asserts every entry
round-trips through Python's ``correction_to_dict`` serializer (defined in
``gen_memory_fixtures.py``) without drift. Locks the snake_case keys, the
ISO-8601 UTC timestamp shape, and the trust=1.0/0.5 numeric encoding.
"""
from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import pytest
from goldenmatch.core.memory.store import Correction
from tests.parity.memory.gen_memory_fixtures import correction_to_dict

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "memory_corrections.json"


@pytest.fixture(scope="module")
def fixture_entries() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_has_12_corrections(fixture_entries):
    assert len(fixture_entries) == 12


def test_snake_case_keys_preserved(fixture_entries):
    expected_keys = {
        "id", "id_a", "id_b", "decision", "source", "trust",
        "field_hash", "record_hash", "original_score",
        "matchkey_name", "reason", "dataset", "created_at",
    }
    for entry in fixture_entries:
        assert set(entry.keys()) == expected_keys, entry["id"]
        assert "idA" not in entry
        assert "fieldHash" not in entry


def test_trust_values_stay_numeric(fixture_entries):
    trust_values = {e["trust"] for e in fixture_entries}
    assert 1.0 in trust_values
    assert 0.5 in trust_values
    for e in fixture_entries:
        assert e["trust"] in (1.0, 0.5), e["id"]


def _entry_to_correction(entry: dict) -> Correction:
    from datetime import datetime
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
        created_at=datetime.fromisoformat(ts).astimezone(UTC),
    )


def test_round_trip_parses_and_reserializes(fixture_entries):
    """Every entry: dict -> Correction -> dict produces an equivalent dict."""
    for entry in fixture_entries:
        c = _entry_to_correction(entry)
        back = correction_to_dict(c)
        # Compare key-by-key so a mismatch surfaces a useful error.
        for key in entry:
            assert back[key] == entry[key], f"{c.id} key={key}"


def test_covers_every_correction_source(fixture_entries):
    sources = {e["source"] for e in fixture_entries}
    assert sources >= {"steward", "boost", "unmerge", "agent", "llm", "api"}


def test_covers_both_decisions_and_hash_shapes(fixture_entries):
    decisions = {e["decision"] for e in fixture_entries}
    assert decisions == {"approve", "reject"}
    empty = [e for e in fixture_entries
             if e["field_hash"] == "" and e["record_hash"] == ""]
    full = [e for e in fixture_entries
            if e["field_hash"] != "" and e["record_hash"] != ""]
    assert len(empty) > 0
    assert len(full) > 0
