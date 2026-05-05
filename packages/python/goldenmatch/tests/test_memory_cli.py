"""Tests for the Learning Memory Python API additions and CLI subgroup (Phase 6)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

import goldenmatch
from goldenmatch.cli.main import app


# ── Python API tests ──


def test_api_add_and_count(tmp_path):
    p = str(tmp_path / "mem.db")
    goldenmatch.add_correction(1, 2, "approve", source="steward", path=p)
    stats = goldenmatch.memory_stats(path=p)
    assert stats["count"] == 1


def test_api_learn_returns_adjustments(tmp_path):
    p = str(tmp_path / "mem.db")
    # Need at least 10 corrections (threshold_min) covering both decisions
    # to produce an adjustment.
    for i in range(6):
        goldenmatch.add_correction(
            i, i + 100, "approve", source="steward",
            matchkey_name="identity", path=p,
        )
    for i in range(6):
        goldenmatch.add_correction(
            200 + i, 300 + i, "reject", source="steward",
            matchkey_name="identity", path=p,
        )
    # Manually patch original_score by reopening — but our API leaves it 0.0.
    # Instead, rewrite via store with non-zero scores so threshold can compute.
    from goldenmatch.core.memory.store import MemoryStore, Correction
    from datetime import datetime
    import uuid
    store = MemoryStore(backend="sqlite", path=p)
    try:
        # Replace existing rows with non-zero original_score
        for i in range(6):
            store.add_correction(Correction(
                id=str(uuid.uuid4()), id_a=i, id_b=i + 100,
                decision="approve", source="steward", trust=1.0,
                field_hash="", record_hash="",
                original_score=0.85 + i * 0.01,
                matchkey_name="identity", reason=None,
                dataset=None, created_at=datetime.now(),
            ))
        for i in range(6):
            store.add_correction(Correction(
                id=str(uuid.uuid4()), id_a=200 + i, id_b=300 + i,
                decision="reject", source="steward", trust=1.0,
                field_hash="", record_hash="",
                original_score=0.50 + i * 0.01,
                matchkey_name="identity", reason=None,
                dataset=None, created_at=datetime.now(),
            ))
    finally:
        store.close()

    adjustments = goldenmatch.learn(path=p)
    assert isinstance(adjustments, list)
    assert len(adjustments) >= 1
    assert any(a.matchkey_name == "identity" for a in adjustments)


def test_api_memory_stats_shape(tmp_path):
    p = str(tmp_path / "mem.db")
    goldenmatch.add_correction(1, 2, "approve", source="steward", path=p)
    stats = goldenmatch.memory_stats(path=p)
    assert set(stats.keys()) >= {"count", "last_learn_time", "adjustments"}
    assert stats["count"] == 1
    assert isinstance(stats["adjustments"], list)


# ── CLI tests ──


def test_cli_memory_stats_runs(tmp_path):
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    goldenmatch.add_correction(1, 2, "approve", source="steward", path=p)
    result = runner.invoke(app, ["memory", "stats", "--path", p])
    assert result.exit_code == 0, result.output
    assert "1" in result.stdout


def test_cli_memory_export_import_roundtrip(tmp_path):
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    csv_out = str(tmp_path / "corrs.csv")

    goldenmatch.add_correction(1, 2, "approve", source="steward", path=p)
    goldenmatch.add_correction(3, 4, "reject", source="steward", path=p)
    goldenmatch.add_correction(5, 6, "approve", source="steward", path=p)
    assert goldenmatch.memory_stats(path=p)["count"] == 3

    # Export
    res = runner.invoke(app, ["memory", "export", csv_out, "--path", p])
    assert res.exit_code == 0, res.output
    assert Path(csv_out).exists()

    # Clear store by deleting the DB file
    Path(p).unlink()

    # Import
    res2 = runner.invoke(app, ["memory", "import", csv_out, "--path", p])
    assert res2.exit_code == 0, res2.output

    stats = goldenmatch.memory_stats(path=p)
    assert stats["count"] == 3

    # Verify the actual pairs survived
    from goldenmatch.core.memory.store import MemoryStore
    store = MemoryStore(backend="sqlite", path=p)
    try:
        corrs = store.get_corrections()
        pairs = {(c.id_a, c.id_b) for c in corrs}
        assert pairs == {(1, 2), (3, 4), (5, 6)}
    finally:
        store.close()


def test_cli_memory_show_renders(tmp_path):
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    goldenmatch.add_correction(
        7, 8, "approve", source="steward", reason="same person", path=p,
    )
    result = runner.invoke(app, ["memory", "show", "7", "8", "--path", p])
    assert result.exit_code == 0, result.output
    assert "7" in result.stdout and "8" in result.stdout
    assert "approve" in result.stdout


def test_cli_memory_learn_runs(tmp_path):
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    # Empty store — learn should still run cleanly with no adjustments.
    result = runner.invoke(app, ["memory", "learn", "--path", p])
    assert result.exit_code == 0, result.output


def test_cli_memory_import_rejects_malformed_csv(tmp_path):
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    res = runner.invoke(app, ["memory", "import", str(bad), "--path", p])
    assert res.exit_code == 1


def test_cli_memory_import_skips_malformed_rows(tmp_path):
    """Mixed valid/invalid rows: header is fine so import proceeds, but each
    invalid row is skipped with a warning printed. The valid rows land in the
    store; the malformed ones do not."""
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    src = tmp_path / "mixed.csv"
    src.write_text(
        "id_a,id_b,decision,source\n"
        "1,2,approve,steward\n"
        "not_an_int,3,approve,steward\n"  # malformed id_a
        "4,not_an_int,reject,steward\n"   # malformed id_b
        "5,6,reject,steward\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["memory", "import", str(src), "--path", p])
    assert res.exit_code == 0, res.output
    # Two malformed rows were skipped; two good rows imported.
    from goldenmatch.core.memory.store import MemoryStore

    store = MemoryStore(backend="sqlite", path=p)
    try:
        items = store.get_corrections()
    finally:
        store.close()
    assert len(items) == 2
    pairs = sorted({(c.id_a, c.id_b) for c in items})
    assert pairs == [(1, 2), (5, 6)]
