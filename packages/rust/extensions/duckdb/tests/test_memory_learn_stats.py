"""Tests for the DuckDB Learning Memory UDFs (Wave 3 SQL-surface expansion).

Exercises:
- goldenmatch_memory_stats on an empty store and after corrections
- goldenmatch_memory_learn structure (count + adjustments) and the
  >= 10-corrections threshold-tuning gate
- Round-trip on the same SQLite file the correction_add UDF writes
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("goldenmatch")

from goldenmatch_duckdb.functions import register


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    register(c)
    try:
        yield c
    finally:
        c.close()


def _add(con, memory_path: str, id_a: int, id_b: int) -> None:
    args = json.dumps({"id_a": id_a, "id_b": id_b, "matchkey_name": "mk"})
    con.execute(
        "SELECT goldenmatch_correction_add(?, ?, ?, ?)",
        ["approve", "ds", memory_path, args],
    ).fetchone()


def test_memory_stats_empty(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    result = json.loads(
        con.sql(f"SELECT goldenmatch_memory_stats('{memory_path}')").fetchone()[0]
    )
    assert result["total_corrections"] == 0
    assert result["last_learn_time"] is None
    assert result["adjustments"] == []


def test_memory_stats_counts_corrections(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    for i in range(3):
        _add(con, memory_path, i, i + 100)
    result = json.loads(
        con.sql(f"SELECT goldenmatch_memory_stats('{memory_path}')").fetchone()[0]
    )
    assert result["total_corrections"] == 3


def test_memory_learn_below_threshold_returns_empty(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    for i in range(3):  # < 10 corrections -> no threshold tuning
        _add(con, memory_path, i, i + 100)
    result = json.loads(
        con.sql(f"SELECT goldenmatch_memory_learn('', '{memory_path}')").fetchone()[0]
    )
    assert result["count"] == 0
    assert result["adjustments"] == []


def test_memory_learn_returns_structured_result(con, tmp_path: Path):
    memory_path = str(tmp_path / "memory.db")
    for i in range(12):  # >= 10 -> learner may emit an adjustment
        _add(con, memory_path, i, i + 100)
    result = json.loads(
        con.sql(f"SELECT goldenmatch_memory_learn('mk', '{memory_path}')").fetchone()[0]
    )
    assert isinstance(result["count"], int)
    assert isinstance(result["adjustments"], list)
    assert result["count"] == len(result["adjustments"])


def test_memory_stats_missing_path_is_soft(con, tmp_path: Path):
    # Empty memory_path defaults to .goldenmatch/memory.db; a fresh/missing
    # store must not raise — it returns zeroed stats or a soft {"error": ...}.
    out = con.sql(
        f"SELECT goldenmatch_memory_stats('{tmp_path / 'nope.db'}')"
    ).fetchone()[0]
    parsed = json.loads(out)
    assert "total_corrections" in parsed or "error" in parsed
