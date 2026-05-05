"""Tests for collision-safe vectorized re-anchor via record_hash."""
from __future__ import annotations

import uuid
from datetime import datetime

import polars as pl

from goldenmatch.core.memory.corrections import (
    apply_corrections,
    build_row_lookup,
    compute_field_hash,
    compute_record_hash,
)
from goldenmatch.core.memory.store import Correction, MemoryStore


def _make_df(rows):
    """rows: list of (row_id, name, zip)"""
    return pl.DataFrame(
        {
            "__row_id__": [r[0] for r in rows],
            "name": [r[1] for r in rows],
            "zip": [r[2] for r in rows],
        }
    )


def _seed_correction(store, df, id_a, id_b, decision, *, fields=("name", "zip")):
    lookup = build_row_lookup(df, list(fields))
    fh = compute_field_hash(lookup[id_a], lookup[id_b])
    rh = f"{compute_record_hash(df, id_a)}:{compute_record_hash(df, id_b)}"
    store.add_correction(
        Correction(
            id=str(uuid.uuid4()),
            id_a=id_a,
            id_b=id_b,
            decision=decision,
            source="steward",
            trust=1.0,
            field_hash=fh,
            record_hash=rh,
            original_score=0.92,
            matchkey_name=None,
            reason=None,
            dataset="t",
            created_at=datetime.now(),
        )
    )


def test_reanchor_after_row_reorder(tmp_path):
    df1 = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
            (3, "Beta Inc", "20002"),
        ]
    )
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    df2 = _make_df(
        [
            (10, "Acme Corp", "10001"),
            (20, "Acme LLC", "10001"),
            (30, "Beta Inc", "20002"),
        ]
    )
    scored = [(10, 20, 0.92), (10, 30, 0.10), (20, 30, 0.10)]
    adjusted, stats = apply_corrections(
        scored, store, df2, ["name", "zip"], dataset="t"
    )

    pair_score = next(s for a, b, s in adjusted if (a, b) == (10, 20))
    assert pair_score == 0.0
    assert stats.applied == 1
    assert stats.stale == 0


def test_reanchor_skips_ambiguous_duplicates(tmp_path):
    df1 = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    df2 = _make_df(
        [
            (10, "Acme Corp", "10001"),
            (11, "Acme Corp", "10001"),
            (20, "Acme LLC", "10001"),
        ]
    )
    scored = [(10, 20, 0.92), (11, 20, 0.92), (10, 11, 1.0)]
    adjusted, stats = apply_corrections(
        scored, store, df2, ["name", "zip"], dataset="t"
    )

    assert all(
        s == orig for (_, _, s), (_, _, orig) in zip(adjusted, scored)
    )
    assert stats.applied == 0
    assert stats.stale_ambiguous == 1


def test_edit_on_matchkey_field_marks_stale(tmp_path):
    df1 = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    df2 = _make_df(
        [
            (1, "ACME CORPORATION", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    scored = [(1, 2, 0.85)]
    adjusted, stats = apply_corrections(
        scored, store, df2, ["name", "zip"], dataset="t"
    )
    assert adjusted[0][2] == 0.85
    assert stats.applied == 0
    assert stats.stale == 1


def test_edit_on_non_matchkey_field_still_applies(tmp_path):
    df1 = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    df1 = df1.with_columns(pl.lit("old_note").alias("note"))
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject", fields=("name", "zip"))

    df2 = df1.with_columns(pl.lit("new_note").alias("note"))
    scored = [(1, 2, 0.92)]
    adjusted, stats = apply_corrections(
        scored, store, df2, ["name", "zip"], dataset="t"
    )
    assert stats.stale == 1


def test_apply_corrections_empty_store_returns_unchanged(tmp_path):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    df = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    scored = [(1, 2, 0.92), (1, 1, 0.5)]
    adjusted, stats = apply_corrections(
        scored, store, df, ["name", "zip"], dataset="t"
    )
    assert adjusted == scored
    assert stats.applied == 0
    assert stats.stale == 0
    assert stats.total_pairs == len(scored)


def test_apply_corrections_missing_row_id_column(tmp_path, caplog):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    df_with_row_id = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    _seed_correction(store, df_with_row_id, 1, 2, "reject")

    df_no_row_id = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC"],
            "zip": ["10001", "10001"],
        }
    )
    scored = [(1, 2, 0.92)]
    with caplog.at_level("WARNING", logger="goldenmatch.memory"):
        adjusted, stats = apply_corrections(
            scored, store, df_no_row_id, ["name", "zip"], dataset="t"
        )
    assert adjusted == scored
    assert stats.applied == 0
    assert any("__row_id__" in rec.message for rec in caplog.records)


def test_unanchorable_corrections_counted(tmp_path):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    # Seed correction with empty record_hash (simulating unmerge-collected correction)
    store.add_correction(
        Correction(
            id=str(uuid.uuid4()),
            id_a=999,
            id_b=1000,
            decision="reject",
            source="unmerge",
            trust=1.0,
            field_hash="",
            record_hash="",
            original_score=0.92,
            matchkey_name=None,
            reason=None,
            dataset="t",
            created_at=datetime.now(),
        )
    )
    df = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    scored = [(1, 2, 0.5)]
    adjusted, stats = apply_corrections(
        scored, store, df, ["name", "zip"], dataset="t"
    )
    assert stats.stale_unanchorable >= 1
    assert (999, 1000) in stats.stale_pairs


def test_reanchor_disabled_falls_back_to_row_id_lookup(tmp_path):
    df1 = _make_df(
        [
            (1, "Acme Corp", "10001"),
            (2, "Acme LLC", "10001"),
        ]
    )
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    df2 = _make_df(
        [
            (10, "Acme Corp", "10001"),
            (20, "Acme LLC", "10001"),
        ]
    )
    scored = [(10, 20, 0.92)]
    adjusted, stats = apply_corrections(
        scored,
        store,
        df2,
        ["name", "zip"],
        dataset="t",
        reanchor=False,
    )
    assert adjusted[0][2] == 0.92
    assert stats.applied == 0
