"""Tests for GoldenPipe v1.2 IdentityResolveStage.

Three layers:

  1. Unit: stage adapter in isolation against pre-built artifacts.
  2. Skip behavior: decide_identity / direct-run guard short-circuit on
     empty clusters.
  3. Determinism: two end-to-end runs through the dedupe + identity
     stages produce the same entity_id set and an equivalent event log.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from goldenpipe.adapters.identity import (
    HAS_IDENTITY,
    IdentityResolveStage,
    decide_identity,
)
from goldenpipe.models.context import PipeContext, StageStatus

pytestmark = pytest.mark.skipif(
    not HAS_IDENTITY,
    reason="requires goldenmatch>=1.15.0 (Identity Graph)",
)


def _cluster(members, score=0.95):
    pair_scores = {}
    ms = list(members)
    for i, a in enumerate(ms):
        for b in ms[i + 1 :]:
            pair_scores[(min(a, b), max(a, b))] = score
    return {
        "members": ms,
        "size": len(ms),
        "oversized": False,
        "pair_scores": pair_scores,
        "confidence": score,
        "bottleneck_pair": (ms[0], ms[1]) if len(ms) >= 2 else None,
        "cluster_quality": "strong",
    }


def _people_df(extra_row: bool = False):
    rows = [
        {"__row_id__": 0, "__source__": "crm", "id": "1",
         "name": "Alice Smith", "email": "a@x.com"},
        {"__row_id__": 1, "__source__": "crm", "id": "2",
         "name": "Alyce Smith", "email": "a@x.com"},
        {"__row_id__": 2, "__source__": "crm", "id": "3",
         "name": "Bob Jones",   "email": "b@y.com"},
    ]
    if extra_row:
        rows.append({"__row_id__": 3, "__source__": "crm", "id": "4",
                     "name": "Alise Smith", "email": "a@x.com"})
    return pl.DataFrame(rows)


# ── 1. Unit ─────────────────────────────────────────────────────────────


def test_stage_writes_summary_and_path(tmp_path: Path):
    db = str(tmp_path / "identity.db")
    df = _people_df()
    ctx = PipeContext(
        df=df,
        artifacts={
            "clusters": {0: _cluster([0, 1]), 1: _cluster([2])},
            "scored_pairs": [(0, 1, 0.95)],
            "matchkey_used": "people_fuzzy",
        },
        stage_config={"path": db, "source_pk_column": "id", "dataset": "u"},
    )
    stage = IdentityResolveStage()
    stage.validate(ctx)
    result = stage.run(ctx)

    assert result.status == StageStatus.SUCCESS
    summary = ctx.artifacts["identity_summary"]
    assert summary["created"] == 2
    assert summary["records_upserted"] == 3
    assert ctx.artifacts["identity_store_path"] == db
    assert ctx.artifacts["conflicts"] == summary["conflicts_flagged"]


def test_stage_propagates_weak_threshold(tmp_path: Path):
    """weak_confidence_threshold from stage_config reaches resolve_clusters."""
    db = str(tmp_path / "weak.db")
    df = _people_df()
    weak_cluster = _cluster([0, 1, 2], score=0.45)
    weak_cluster["confidence"] = 0.45
    ctx = PipeContext(
        df=df,
        artifacts={
            "clusters": {0: weak_cluster},
            "scored_pairs": [(0, 1, 0.95), (1, 2, 0.41), (0, 2, 0.94)],
        },
        stage_config={"path": db, "source_pk_column": "id",
                      "weak_confidence_threshold": 0.9},
    )
    IdentityResolveStage().run(ctx)
    assert ctx.artifacts["conflicts"] >= 1


# ── 2. Skip behavior ────────────────────────────────────────────────────


def test_decide_identity_skips_when_no_clusters():
    ctx = PipeContext(df=pl.DataFrame(), artifacts={})
    decision = decide_identity(ctx)
    assert "goldenmatch.identity_resolve" in decision.skip
    assert "no clusters" in decision.reason


def test_decide_identity_runs_when_clusters_exist():
    ctx = PipeContext(df=pl.DataFrame(), artifacts={"clusters": {0: _cluster([0])}})
    decision = decide_identity(ctx)
    assert decision.skip == []


def test_stage_skips_when_clusters_missing(tmp_path: Path):
    ctx = PipeContext(df=_people_df(), artifacts={})  # no clusters
    result = IdentityResolveStage().run(ctx)
    assert result.status == StageStatus.SKIPPED
    assert result.decision is not None
    assert "goldenmatch.identity_resolve" in result.decision.skip


# ── 3. Determinism across runs ──────────────────────────────────────────


def test_two_runs_produce_stable_entity_ids(tmp_path: Path):
    """Run 1 mints identities. Run 2 with one extra record absorbs it
    into the existing identity rather than minting fresh IDs."""
    from goldenmatch.identity import IdentityStore, find_by_record

    db = str(tmp_path / "stable.db")

    # Run 1
    ctx1 = PipeContext(
        df=_people_df(),
        artifacts={
            "clusters": {0: _cluster([0, 1]), 1: _cluster([2])},
            "scored_pairs": [(0, 1, 0.95)],
            "matchkey_used": "people_fuzzy",
        },
        stage_config={"path": db, "source_pk_column": "id", "dataset": "stable"},
        metadata={"run_id": "demo-r1"},
    )
    IdentityResolveStage().run(ctx1)

    with IdentityStore(path=db) as s:
        alice = find_by_record(s, "crm:1")
        assert alice is not None
        alice_eid_r1 = alice.node.entity_id

    # Run 2 -- adds crm:4 to Alice's cluster
    ctx2 = PipeContext(
        df=_people_df(extra_row=True),
        artifacts={
            "clusters": {0: _cluster([0, 1, 3]), 1: _cluster([2])},
            "scored_pairs": [(0, 1, 0.95), (0, 3, 0.93), (1, 3, 0.92)],
            "matchkey_used": "people_fuzzy",
        },
        stage_config={"path": db, "source_pk_column": "id", "dataset": "stable"},
        metadata={"run_id": "demo-r2"},
    )
    IdentityResolveStage().run(ctx2)

    with IdentityStore(path=db) as s:
        # Alice's entity_id is stable across the two runs.
        alice_r2 = find_by_record(s, "crm:1")
        assert alice_r2.node.entity_id == alice_eid_r1
        # New record landed on the same identity (absorb).
        new_rec = find_by_record(s, "crm:4")
        assert new_rec.node.entity_id == alice_eid_r1


def test_replay_same_run_id_is_idempotent(tmp_path: Path):
    """Two runs with the same metadata['run_id'] don't double-emit events."""
    from goldenmatch.identity import IdentityStore
    from goldenmatch.identity import history as identity_history

    db = str(tmp_path / "idem.db")

    def _run():
        ctx = PipeContext(
            df=_people_df(),
            artifacts={
                "clusters": {0: _cluster([0, 1])},
                "scored_pairs": [(0, 1, 0.95)],
                "matchkey_used": "mk",
            },
            stage_config={"path": db, "source_pk_column": "id"},
            metadata={"run_id": "idem-run"},
        )
        IdentityResolveStage().run(ctx)

    _run()
    _run()  # replay -- should be a no-op for event emission

    with IdentityStore(path=db) as s:
        eid = s.find_entity_by_record("crm:1")
        events = identity_history(s, eid)
        # `created` event guarded by has_run_event -> still 1
        created = [e for e in events if e["kind"] == "created"]
        assert len(created) == 1


def test_entry_point_registered():
    """The stage is discoverable via the goldenpipe.stages entry-point."""
    import importlib.metadata as md

    eps = md.entry_points().select(group="goldenpipe.stages")
    names = {e.name for e in eps}
    assert "goldenmatch.identity_resolve" in names, (
        f"identity_resolve missing from entry-points: {names}"
    )
