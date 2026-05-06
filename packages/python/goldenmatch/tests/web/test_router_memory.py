"""Memory store browser endpoints — corrections + stats + learn trigger.

These tests chdir into a tmp dir before posting labels so the
MemoryStore default path (``.goldenmatch/memory.db``, CWD-relative) lands
in an isolated location instead of polluting the repo's working dir.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_client(client, tmp_path: Path, monkeypatch):
    """`client` plus a chdir into tmp_path so memory writes don't leak."""
    monkeypatch.chdir(tmp_path)
    return client


def _label_pair(client, a: int, b: int, label: str = "match") -> None:
    client.post(
        "/api/v1/labels",
        json={"row_id_a": a, "row_id_b": b, "label": label},
    )


def test_corrections_empty_initially(isolated_client):
    body = isolated_client.get("/api/v1/memory/corrections").json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["truncated"] is False


def test_label_mirrors_to_memory_and_corrections_endpoint_returns_it(isolated_client):
    _label_pair(isolated_client, 0, 1, "match")
    _label_pair(isolated_client, 0, 2, "non_match")
    body = isolated_client.get("/api/v1/memory/corrections").json()
    assert body["total"] == 2
    items = body["items"]
    decisions = {(c["id_a"], c["id_b"]): c["decision"] for c in items}
    # labels.py maps "match" → "merge", "non_match" → "reject".
    assert decisions[(0, 1)] == "merge"
    assert decisions[(0, 2)] == "reject"
    # Steward source carries trust=1.0 in HIGH_TRUST_SOURCES.
    assert all(c["trust"] == 1.0 for c in items)
    # Hashes intentionally not in the wire shape.
    assert all("field_hash" not in c and "record_hash" not in c for c in items)


def test_stats_reflects_correction_count(isolated_client):
    _label_pair(isolated_client, 0, 1)
    body = isolated_client.get("/api/v1/memory/stats").json()
    assert body["count"] == 1
    # No learn pass yet → last_learn_time should be None.
    assert body["last_learn_time"] is None
    assert body["adjustments"] == []


def test_learn_returns_no_adjustments_below_min(isolated_client):
    """Threshold tuning needs ≥10 corrections (LearningConfig default).
    With one correction the pass should produce zero adjustments rather
    than erroring."""
    _label_pair(isolated_client, 0, 1)
    body = isolated_client.post("/api/v1/memory/learn").json()
    assert body["adjustments"] == []
    assert body["matchkey_filter"] is None


def test_corrections_limit_caps_results(isolated_client):
    for i in range(5):
        _label_pair(isolated_client, 0, i + 1)
    body = isolated_client.get("/api/v1/memory/corrections?limit=2").json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["truncated"] is True
