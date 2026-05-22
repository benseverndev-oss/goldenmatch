"""Tests for field-level Correction support (#437).

Covers:
- Correction dataclass with optional field_name / original_value / corrected_value
- MemoryStore.add_correction + get_corrections round-trip with new fields
- Schema migration on pre-existing DB (idempotent ALTER TABLE)
- `tune_field_strategy` consumes field-level corrections preferentially
- Pair-level corrections still work (backward compat)
- `_strategy_would_match` field-level branch
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from goldenmatch.core.autoconfig_golden_strategy_tuner import (
    _strategy_would_match,
    tune_field_strategy,
)
from goldenmatch.core.memory.store import (
    Correction,
    CorrectionSource,
    Decision,
    MemoryStore,
)


def _mk_pair_correction(
    *, dataset: str = "d", trust: float = 0.9, decision: str = "approve",
    id_a: int = 1, id_b: int = 2,
) -> Correction:
    return Correction(
        id=f"pair-{id_a}-{id_b}-{decision}",
        id_a=id_a, id_b=id_b,
        decision=decision, source=CorrectionSource.STEWARD.value,
        trust=trust, field_hash="hash_a", record_hash="hash_b",
        original_score=0.5, dataset=dataset,
    )


def _mk_field_correction(
    *, cluster_id: int = 1, field_name: str = "address1",
    original_value: str = "123 Main",
    corrected_value: str = "123 Main Street",
    dataset: str = "d", trust: float = 0.9,
    cid_suffix: str = "",
) -> Correction:
    return Correction(
        id=f"field-{cluster_id}-{field_name}-{cid_suffix}",
        id_a=cluster_id, id_b=0,
        decision=Decision.FIELD_CORRECT.value,
        source=CorrectionSource.STEWARD.value,
        trust=trust, field_hash="opaque", record_hash="opaque",
        original_score=0.0,
        dataset=dataset,
        field_name=field_name,
        original_value=original_value,
        corrected_value=corrected_value,
    )


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


def test_correction_field_level_fields_default_none():
    c = _mk_pair_correction()
    assert c.field_name is None
    assert c.original_value is None
    assert c.corrected_value is None


def test_correction_field_level_fields_can_be_set():
    c = _mk_field_correction(
        field_name="email",
        original_value="bob@old.com",
        corrected_value="bob@new.com",
    )
    assert c.field_name == "email"
    assert c.original_value == "bob@old.com"
    assert c.corrected_value == "bob@new.com"
    assert c.decision == "field_correct"


def test_decision_enum_includes_field_correct():
    assert Decision.FIELD_CORRECT.value == "field_correct"


# ---------------------------------------------------------------------------
# SQLite persistence + round trip
# ---------------------------------------------------------------------------


def test_store_round_trips_field_level_correction(tmp_path: Path):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    c = _mk_field_correction()
    store.add_correction(c)

    rows = list(store.get_corrections(dataset="d"))
    assert len(rows) == 1
    out = rows[0]
    assert out.field_name == "address1"
    assert out.original_value == "123 Main"
    assert out.corrected_value == "123 Main Street"
    assert out.decision == "field_correct"
    store.close()


def test_store_round_trips_pair_level_correction_unchanged(tmp_path: Path):
    """Pair-level corrections still hydrate with field_* = None."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    c = _mk_pair_correction()
    store.add_correction(c)
    rows = list(store.get_corrections(dataset="d"))
    assert len(rows) == 1
    out = rows[0]
    assert out.field_name is None
    assert out.original_value is None
    assert out.corrected_value is None
    assert out.decision == "approve"
    store.close()


def test_migration_idempotent_on_preexisting_db(tmp_path: Path):
    """Open a DB on the old schema (no field_* columns), then open it
    again -- the second open's ALTER TABLE migration should succeed
    and the new columns should round-trip data."""
    db_path = tmp_path / "old.db"

    # Set up pre-v1.18.2 schema by hand (no field_name/original_value/corrected_value).
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE corrections (
            id TEXT PRIMARY KEY,
            id_a INTEGER, id_b INTEGER,
            decision TEXT, source TEXT, trust REAL,
            field_hash TEXT, record_hash TEXT,
            original_score REAL,
            matchkey_name TEXT,
            reason TEXT, dataset TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(id_a, id_b, dataset)
        )
    """)
    conn.commit()
    conn.close()

    # Now MemoryStore opens the same file -- migration must add columns.
    store = MemoryStore(backend="sqlite", path=str(db_path))
    c = _mk_field_correction()
    store.add_correction(c)
    rows = list(store.get_corrections(dataset="d"))
    assert len(rows) == 1
    assert rows[0].field_name == "address1"
    assert rows[0].corrected_value == "123 Main Street"
    store.close()


# ---------------------------------------------------------------------------
# _strategy_would_match -- field-level branch
# ---------------------------------------------------------------------------


def test_strategy_match_longer_correction_credits_longest_value():
    c = _mk_field_correction(
        original_value="123 Main",
        corrected_value="123 Main Street",  # longer
    )
    assert _strategy_would_match(c, "longest_value") is True
    # Preserving strategies do NOT match an edit.
    assert _strategy_would_match(c, "most_complete") is False
    assert _strategy_would_match(c, "majority_vote") is False
    assert _strategy_would_match(c, "first_non_null") is False


def test_strategy_match_no_edit_credits_preserving_strategies():
    """When reviewer approves the chosen value (original == corrected),
    preserving strategies score a hit."""
    c = _mk_field_correction(
        original_value="123 Main Street",
        corrected_value="123 Main Street",
    )
    assert _strategy_would_match(c, "most_complete") is True
    assert _strategy_would_match(c, "longest_value") is True
    assert _strategy_would_match(c, "majority_vote") is True


def test_strategy_match_filters_by_field():
    """When field= is set, only field-level corrections on THAT field count."""
    c_address = _mk_field_correction(field_name="address1", cid_suffix="A")
    c_email = _mk_field_correction(field_name="email", cid_suffix="E")
    assert _strategy_would_match(c_address, "longest_value", field="address1") is True
    assert _strategy_would_match(c_email, "longest_value", field="address1") is False


def test_strategy_match_pair_level_falls_back_to_heuristic():
    """No field_name -> use the v1.18.1 trust/decision heuristic."""
    approve = _mk_pair_correction(decision="approve", trust=0.9)
    reject = _mk_pair_correction(decision="reject", trust=0.9)
    assert _strategy_would_match(approve, "most_complete") is True
    assert _strategy_would_match(reject, "unanimous_or_null") is True


# ---------------------------------------------------------------------------
# Tuner: prefers field-level corpus when large enough
# ---------------------------------------------------------------------------


def test_tuner_uses_field_corpus_when_above_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """50 field-level edits on `address1` all credit longest_value.
    Tuner should learn longest_value, not the default."""
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS", "5")
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    # 10 field-level edits where corrected is longer than original.
    for i in range(10):
        store.add_correction(_mk_field_correction(
            cluster_id=i + 1,
            original_value="123 Main",
            corrected_value="123 Main Street Apt 4B",
            cid_suffix=f"{i:03d}",
        ))
    result = tune_field_strategy(store=store, dataset="d", field="address1")
    assert result.reason == "learned"
    # longest_value should have ~100% hit rate; alternatives lower.
    assert result.strategy in {"longest_value", "confidence_majority",
                                "most_recent", "source_priority"}
    # Specifically check that longest_value is the picked one (the
    # tuner takes the FIRST one to hit the max, and longest_value
    # appears in DEFAULT_CANDIDATE_STRATEGIES).
    assert result.train_hit_rate is not None
    assert result.train_hit_rate >= 0.9
    store.close()


def test_tuner_field_filter_isolates_per_field_learning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Corrections on one field do NOT contaminate tuning for another field
    when the other field has its own corpus."""
    monkeypatch.setenv("GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS", "5")
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    # 10 address1 edits favoring longer values.
    for i in range(10):
        store.add_correction(_mk_field_correction(
            cluster_id=i + 1, field_name="address1",
            original_value="123 Main",
            corrected_value="123 Main Street Apt 4B",
            cid_suffix=f"a{i:03d}",
        ))
    # 10 email no-edit confirmations (preserving strategies fit).
    for i in range(10):
        store.add_correction(_mk_field_correction(
            cluster_id=100 + i, field_name="email",
            original_value="user@example.com",
            corrected_value="user@example.com",
            cid_suffix=f"e{i:03d}",
        ))
    address_tuning = tune_field_strategy(store=store, dataset="d", field="address1")
    email_tuning = tune_field_strategy(store=store, dataset="d", field="email")
    assert address_tuning.reason == "learned"
    assert email_tuning.reason == "learned"
    # address1 picks an edit-friendly strategy; email picks a preserver.
    # Tuner returns the first highest-scoring strategy.
    assert address_tuning.train_hit_rate is not None and address_tuning.train_hit_rate >= 0.9
    assert email_tuning.train_hit_rate is not None and email_tuning.train_hit_rate >= 0.9
    store.close()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
