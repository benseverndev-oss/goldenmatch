"""Cross-language SQLite fixture parity for Learning Memory.

Opens the shared ``memory.db`` (committed alongside the JSON fixture; written
by the generator's ``--rebuild-db`` mode) via Python ``MemoryStore`` and
asserts every row matches the canonical JSON entry. Locks the on-disk schema
(column names, types, ISO timestamp encoding) so a ``.db`` written by either
language is readable by the other.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from goldenmatch.core.memory.store import MemoryStore
from tests.parity.memory.gen_memory_fixtures import correction_to_dict

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DB_FIXTURE = FIXTURE_DIR / "memory.db"
JSON_FIXTURE = FIXTURE_DIR / "memory_corrections.json"


@pytest.fixture(scope="module")
def expected_entries() -> list[dict]:
    return json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def store_copy(tmp_path) -> MemoryStore:
    """Copy the committed memory.db into tmp_path so the test never mutates
    the fixture (sqlite opens read-write by default)."""
    if not DB_FIXTURE.exists():
        pytest.skip(f"fixture missing: {DB_FIXTURE}")
    dest = tmp_path / "memory.db"
    shutil.copy(DB_FIXTURE, dest)
    store = MemoryStore(backend="sqlite", path=str(dest))
    yield store
    store.close()


def test_db_contains_all_12_corrections(store_copy, expected_entries):
    rows = store_copy.get_corrections()
    assert len(rows) == len(expected_entries)


def test_every_correction_matches_json(store_copy, expected_entries):
    rows = {c.id: c for c in store_copy.get_corrections()}
    for entry in expected_entries:
        c = rows.get(entry["id"])
        assert c is not None, f"missing id {entry['id']}"
        back = correction_to_dict(c)
        for key in entry:
            assert back[key] == entry[key], f"{entry['id']} key={key}"


def test_dataset_scoping(store_copy, expected_entries):
    parity = store_copy.get_corrections(dataset="parity_test")
    expected_parity = [e for e in expected_entries if e["dataset"] == "parity_test"]
    assert len(parity) == len(expected_parity)


def test_count_corrections(store_copy, expected_entries):
    assert store_copy.count_corrections() == len(expected_entries)
