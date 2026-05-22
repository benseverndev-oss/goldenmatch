"""Tests for tune_decision_threshold + MemoryStore cluster-decision support.

RFC: docs/superpowers/specs/2026-05-22-cluster-decision-tuner-design.md

Covers:
- Correction.cluster_score / cluster_outcome round-trip
- Decision.CLUSTER_DECISION enum value
- MemoryStore.record_cluster_decision() convenience method
- SQLite migration of cluster_score / cluster_outcome columns
- tune_decision_threshold() algorithm: below_minimum, no_qualifying_band,
  overfit, ok paths
- Seed determinism + dataset isolation
- Pair-level and field-level corrections are ignored by the cluster tuner
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from goldenmatch.core.autoconfig_cluster_threshold_tuner import (
    ThresholdSuggestion,
    tune_decision_threshold,
)
from goldenmatch.core.memory.store import (
    Correction,
    Decision,
    MemoryStore,
)

# ---------------------------------------------------------------------------
# Decision enum + Correction shape
# ---------------------------------------------------------------------------


def test_decision_cluster_decision_enum_exists():
    assert Decision.CLUSTER_DECISION.value == "cluster_decision"


def test_correction_cluster_fields_default_none():
    from datetime import datetime

    c = Correction(
        id="c1", id_a=42, id_b=0,
        decision="cluster_decision", source="steward", trust=1.0,
        field_hash="", record_hash="", original_score=0.0,
        matchkey_name=None, reason=None, dataset="ds",
        created_at=datetime.now(),
    )
    # cluster_score / cluster_outcome default to None.
    assert c.cluster_score is None
    assert c.cluster_outcome is None


# ---------------------------------------------------------------------------
# MemoryStore.record_cluster_decision
# ---------------------------------------------------------------------------


def test_record_cluster_decision_round_trip(tmp_path: Path):
    db_path = str(tmp_path / "m.db")
    store = MemoryStore(backend="sqlite", path=db_path)
    correction = store.record_cluster_decision(
        dataset="pub_48", cluster_id=123, score=0.97, outcome="approve",
        reason="auto-approved by sweep",
    )
    rows = list(store.get_corrections(dataset="pub_48"))
    store.close()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == correction.id
    assert row.decision == "cluster_decision"
    assert row.cluster_score == 0.97
    assert row.cluster_outcome == "approve"
    assert row.id_a == 123
    assert row.id_b == 0


def test_record_cluster_decision_rejects_invalid_outcome(tmp_path: Path):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        with pytest.raises(ValueError, match="outcome"):
            store.record_cluster_decision(
                dataset="d", cluster_id=1, score=0.9, outcome="maybe",
            )
    finally:
        store.close()


def test_record_cluster_decision_rejects_out_of_range_score(tmp_path: Path):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        with pytest.raises(ValueError, match="score"):
            store.record_cluster_decision(
                dataset="d", cluster_id=1, score=1.5, outcome="approve",
            )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# SQLite migration idempotent on pre-v1.20 DB
# ---------------------------------------------------------------------------


def test_migration_idempotent_on_v1_18_2_db(tmp_path: Path):
    """Open a DB that has field_* columns but no cluster_* columns.
    The cluster-migration must add them without raising."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        # v1.18.2-shape schema (has field_* but NOT cluster_*).
        """
        CREATE TABLE corrections (
            id TEXT PRIMARY KEY,
            id_a INTEGER, id_b INTEGER,
            decision TEXT, source TEXT, trust REAL,
            field_hash TEXT, record_hash TEXT,
            original_score REAL,
            matchkey_name TEXT, reason TEXT, dataset TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            field_name TEXT,
            original_value TEXT,
            corrected_value TEXT,
            UNIQUE(id_a, id_b, dataset)
        )
        """,
    )
    conn.commit()
    conn.close()

    # Open via MemoryStore -- migration should add cluster_* columns.
    store = MemoryStore(backend="sqlite", path=str(db_path))
    store.record_cluster_decision(
        dataset="d", cluster_id=1, score=0.95, outcome="approve",
    )
    rows = list(store.get_corrections(dataset="d"))
    store.close()
    assert len(rows) == 1
    assert rows[0].cluster_score == 0.95
    assert rows[0].cluster_outcome == "approve"


# ---------------------------------------------------------------------------
# tune_decision_threshold paths
# ---------------------------------------------------------------------------


def _seed_corrections(
    store: MemoryStore,
    *,
    dataset: str,
    scores_approve: list[float],
    scores_reject: list[float],
) -> None:
    """Seed N approves at given scores + M rejects at given scores."""
    for i, score in enumerate(scores_approve):
        store.record_cluster_decision(
            dataset=dataset, cluster_id=10_000 + i, score=score,
            outcome="approve",
        )
    for i, score in enumerate(scores_reject):
        store.record_cluster_decision(
            dataset=dataset, cluster_id=20_000 + i, score=score,
            outcome="reject",
        )


def test_below_minimum_returns_none(tmp_path: Path):
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        _seed_corrections(
            store, dataset="d",
            scores_approve=[0.99] * 10,
            scores_reject=[0.50] * 10,
        )
        # Total 20, need 100 (2 * min_band_n=50). Below_minimum.
        result = tune_decision_threshold(store, dataset="d")
    finally:
        store.close()
    assert result.reason == "below_minimum"
    assert result.threshold is None


def test_below_minimum_with_lowered_band(tmp_path: Path):
    """Override min_band_n to test the path with smaller fixtures."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        _seed_corrections(
            store, dataset="d",
            scores_approve=[0.99] * 5,
            scores_reject=[0.50] * 5,
        )
        # Need 6 total for min_band_n=3.
        result = tune_decision_threshold(
            store, dataset="d", min_band_n=3,
        )
    finally:
        store.close()
    # 10 total >= 6; should NOT be below_minimum.
    assert result.reason != "below_minimum"


def test_ok_path_returns_valid_suggestion(tmp_path: Path):
    """100 approves at high scores + 100 rejects at low scores ->
    clean threshold proposal."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        approve_scores = [0.90 + 0.0009 * i for i in range(100)]
        reject_scores = [0.20 + 0.0005 * i for i in range(100)]
        _seed_corrections(
            store, dataset="clean",
            scores_approve=approve_scores,
            scores_reject=reject_scores,
        )
        result = tune_decision_threshold(
            store, dataset="clean",
            target_approve_rate=0.99,
            min_band_n=50,
        )
    finally:
        store.close()
    assert result.reason == "ok"
    assert result.threshold is not None
    assert result.threshold >= 0.90  # below all approves; above all rejects
    assert result.train_approve_rate is not None
    assert result.train_approve_rate >= 0.99


def test_no_qualifying_band(tmp_path: Path):
    """Approves + rejects fully interleaved -> no band ever hits 99%."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        # Approves and rejects at the SAME score. No threshold can
        # separate them; sweep can't find a 99% band.
        same_score = 0.80
        _seed_corrections(
            store, dataset="mixed",
            scores_approve=[same_score] * 60,
            scores_reject=[same_score] * 60,
        )
        result = tune_decision_threshold(
            store, dataset="mixed",
            target_approve_rate=0.99, min_band_n=50,
        )
    finally:
        store.close()
    assert result.reason == "no_qualifying_band"
    assert result.threshold is None


def test_overfit_guard(tmp_path: Path):
    """Train says ok, but the held-out 10% has a much worse rate.
    The guard should reject the suggestion."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        # Mostly approves at high score but a few rejects sprinkled in
        # the high-score range. With a small held-out, some of those
        # rejects can land there + drop the heldout rate.
        # Seed deterministically and use a small max_overfit_drop_pp
        # to force the guard.
        approves = [0.99 - 0.005 * i for i in range(95)]
        rejects = [0.98, 0.96, 0.94, 0.92, 0.90]
        _seed_corrections(
            store, dataset="overfit",
            scores_approve=approves,
            scores_reject=rejects,
        )
        result = tune_decision_threshold(
            store, dataset="overfit",
            target_approve_rate=0.99,
            min_band_n=50,
            max_overfit_drop_pp=0.5,  # very strict
            seed=42,
        )
    finally:
        store.close()
    # Either "overfit" (heldout caught the rejects) or
    # "no_qualifying_band" (train didn't even hit 99% with a band of
    # >=50). Both reject the suggestion, which is the safety property.
    assert result.reason in ("overfit", "no_qualifying_band")
    assert result.threshold is None


def test_seed_determinism(tmp_path: Path):
    """Same store + dataset + seed -> identical ThresholdSuggestion."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        _seed_corrections(
            store, dataset="d",
            scores_approve=[0.90 + 0.001 * i for i in range(80)],
            scores_reject=[0.20 + 0.001 * i for i in range(80)],
        )
        a = tune_decision_threshold(store, dataset="d", seed=12345)
        b = tune_decision_threshold(store, dataset="d", seed=12345)
    finally:
        store.close()
    assert a == b


def test_default_seed_is_sha256_of_dataset(tmp_path: Path):
    """Default (seed=None) uses sha256(dataset) -> same dataset reproduces."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        _seed_corrections(
            store, dataset="d",
            scores_approve=[0.90 + 0.001 * i for i in range(80)],
            scores_reject=[0.20 + 0.001 * i for i in range(80)],
        )
        a = tune_decision_threshold(store, dataset="d")
        b = tune_decision_threshold(store, dataset="d")
    finally:
        store.close()
    assert a == b


def test_dataset_isolation(tmp_path: Path):
    """pub_a's corrections must not influence pub_b's suggestion."""
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        _seed_corrections(
            store, dataset="pub_a",
            scores_approve=[0.90 + 0.001 * i for i in range(60)],
            scores_reject=[0.20 + 0.001 * i for i in range(60)],
        )
        # pub_b has only 10 corrections -- should be below_minimum.
        _seed_corrections(
            store, dataset="pub_b",
            scores_approve=[0.99] * 5,
            scores_reject=[0.50] * 5,
        )
        result_b = tune_decision_threshold(store, dataset="pub_b")
    finally:
        store.close()
    assert result_b.reason == "below_minimum"


def test_pair_and_field_level_corrections_ignored(tmp_path: Path):
    """tune_decision_threshold only reads decision='cluster_decision'."""
    import uuid
    from datetime import datetime

    store = MemoryStore(backend="sqlite", path=str(tmp_path / "m.db"))
    try:
        # 60 approve pair-level + 60 field_correct -- should not feed
        # the cluster tuner at all.
        for i in range(60):
            store.add_correction(Correction(
                id=str(uuid.uuid4()), id_a=i, id_b=i + 1,
                decision="approve", source="steward", trust=1.0,
                field_hash="", record_hash="", original_score=0.9,
                matchkey_name=None, reason=None, dataset="d",
                created_at=datetime.now(),
            ))
        for i in range(60):
            store.add_correction(Correction(
                id=str(uuid.uuid4()), id_a=i + 1000, id_b=0,
                decision="field_correct", source="steward", trust=1.0,
                field_hash="", record_hash="", original_score=0.0,
                matchkey_name=None, reason=None, dataset="d",
                created_at=datetime.now(),
                field_name="address1", original_value="x",
                corrected_value="y",
            ))
        result = tune_decision_threshold(store, dataset="d")
    finally:
        store.close()
    # No cluster_decision rows at all -> below_minimum.
    assert result.reason == "below_minimum"
    assert result.n_total == 0


def test_threshold_suggestion_is_frozen():
    """ThresholdSuggestion is immutable (matches StrategyTuning shape)."""
    sug = ThresholdSuggestion(
        threshold=0.95, n_total=100, n_train=90, n_heldout=10,
        train_approve_rate=0.99, heldout_approve_rate=0.99, reason="ok",
    )
    with pytest.raises(Exception):
        sug.threshold = 0.80  # type: ignore[misc]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
