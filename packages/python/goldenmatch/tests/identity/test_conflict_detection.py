"""Automatic conflict detection (v2.1).

Two signals are exercised:

  1. **Weak bottleneck** -- a cluster with confidence below the configured
     threshold has its weakest-link pair recorded as a ``conflicts_with``
     edge.
  2. **Merge-with-prior-conflict carry-forward** -- merging an identity
     that previously had a ``conflicts_with`` edge surfaces the same
     conflict on the winning entity.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import (
    EdgeKind,
    IdentityNode,
    IdentityStore,
    SourceRecord,
    new_entity_id,
    resolve_clusters,
)
from goldenmatch.identity.model import EvidenceEdge


@pytest.fixture()
def store(tmp_path):
    p = str(tmp_path / "identity.db")
    s = IdentityStore(path=p)
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": r.get("__source__", "src")}
        for k, v in r.items():
            if not k.startswith("__"):
                rec[k] = v
        out.append(rec)
    return pl.DataFrame(out)


def _weak_cluster(members, pair_scores, confidence, bottleneck):
    return {
        "members": list(members),
        "size": len(members),
        "oversized": False,
        "pair_scores": pair_scores,
        "confidence": confidence,
        "bottleneck_pair": bottleneck,
        "cluster_quality": "weak",
    }


def test_weak_bottleneck_emits_conflict(store):
    df = _df([{"id": "1"}, {"id": "2"}, {"id": "3"}])
    cluster = _weak_cluster(
        members=[0, 1, 2],
        pair_scores={(0, 1): 0.95, (0, 2): 0.94, (1, 2): 0.41},
        confidence=0.45,           # below default threshold (0.6)
        bottleneck=(1, 2),
    )
    summary = resolve_clusters(
        {0: cluster}, df, [(0, 1, 0.95), (0, 2, 0.94), (1, 2, 0.41)],
        "wd", store, run_name="r1", source_pk_col="id",
    )
    assert summary.conflicts_flagged == 1
    conflicts = store.find_conflicts()
    assert len(conflicts) == 1
    edge = conflicts[0]
    # bottleneck=(1, 2) -> __row_id__ 1 = id "2", __row_id__ 2 = id "3"
    assert {edge.record_a_id, edge.record_b_id} == {"src:2", "src:3"}
    assert edge.negative_evidence is not None
    assert edge.negative_evidence["reason"] == "weak_cluster_bottleneck"
    assert edge.negative_evidence["cluster_confidence"] == pytest.approx(0.45)


def test_strong_cluster_emits_no_conflict(store):
    df = _df([{"id": "1"}, {"id": "2"}])
    cluster = {
        "members": [0, 1], "size": 2, "oversized": False,
        "pair_scores": {(0, 1): 0.98}, "confidence": 0.98,
        "bottleneck_pair": (0, 1), "cluster_quality": "strong",
    }
    summary = resolve_clusters(
        {0: cluster}, df, [(0, 1, 0.98)],
        "wd", store, run_name="r1", source_pk_col="id",
    )
    assert summary.conflicts_flagged == 0
    assert store.find_conflicts() == []


def test_weak_threshold_zero_disables_detection(store):
    df = _df([{"id": "1"}, {"id": "2"}, {"id": "3"}])
    cluster = _weak_cluster(
        members=[0, 1, 2],
        pair_scores={(0, 1): 0.55, (1, 2): 0.42, (0, 2): 0.51},
        confidence=0.32, bottleneck=(1, 2),
    )
    summary = resolve_clusters(
        {0: cluster}, df, [(0, 1, 0.55), (1, 2, 0.42), (0, 2, 0.51)],
        "wd", store, run_name="r", source_pk_col="id",
        weak_confidence_threshold=0.0,
    )
    assert summary.conflicts_flagged == 0


def test_merge_carries_forward_prior_conflict(store):
    """Two identities pre-loaded with a conflict between their members; a
    subsequent run clusters those records together, the resolver merges
    the identities, and the original conflict is carried forward on the
    winning entity."""
    eid1 = new_entity_id()
    eid2 = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid1, dataset="d"))
    store.upsert_identity(IdentityNode(entity_id=eid2, dataset="d"))
    store.upsert_record(SourceRecord("src:A", "src", "A", "h1", entity_id=eid1, dataset="d"))
    store.upsert_record(SourceRecord("src:B", "src", "B", "h2", entity_id=eid2, dataset="d"))
    store.add_edge(EvidenceEdge(
        entity_id=eid2, record_a_id="src:A", record_b_id="src:B",
        kind=EdgeKind.CONFLICTS_WITH.value, score=0.30,
        matchkey_name="prior", run_name="r0", dataset="d",
    ))

    # Now a new run clusters them together -> merge.
    df = _df([{"id": "A"}, {"id": "B"}])
    cluster = {
        "members": [0, 1], "size": 2, "oversized": False,
        "pair_scores": {(0, 1): 0.91}, "confidence": 0.91,
        "bottleneck_pair": (0, 1), "cluster_quality": "strong",
    }
    summary = resolve_clusters(
        {0: cluster}, df, [(0, 1, 0.91)],
        "wd", store, run_name="r1", source_pk_col="id", dataset="d",
    )
    assert summary.merged == 1
    # Old conflict carried forward onto the merged identity.
    assert summary.conflicts_flagged >= 1
    winner_eid = store.find_entity_by_record("src:A")
    winner_conflicts = [
        e for e in store.edges_for_entity(winner_eid)
        if e.kind == EdgeKind.CONFLICTS_WITH.value
    ]
    assert len(winner_conflicts) >= 1
    carried = next(
        (e for e in winner_conflicts
         if e.negative_evidence and e.negative_evidence.get("reason") == "carried_forward_from_merge"),
        None,
    )
    assert carried is not None
    assert carried.negative_evidence["from_entity"] in {eid1, eid2}


def test_pipeline_e2e_emits_conflicts(tmp_path):
    """End-to-end via run_dedupe_df: low-threshold matchkey produces a weak
    cluster, the resolver flags it, the summary reports it."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        IdentityConfig,
        MatchkeyConfig,
        MatchkeyField,
        OutputConfig,
    )
    from goldenmatch.core.pipeline import run_dedupe_df

    df = pl.DataFrame({
        "id":    ["1", "2", "3"],
        # Two names that barely cluster, one that drags the cluster down.
        "name":  ["Alice Smith", "Alyce Smith", "Aleseia X"],
        "email": ["a@x.com", "a@x.com", "a@x.com"],
        "zip":   ["12345"] * 3,
    })
    db = str(tmp_path / "id.db")
    cfg = GoldenMatchConfig(
        output=OutputConfig(run_name="r"),
        matchkeys=[MatchkeyConfig(
            name="loose", type="weighted", threshold=0.50,
            fields=[
                MatchkeyField(field="name",  scorer="jaro_winkler", weight=0.7),
                MatchkeyField(field="email", scorer="exact",        weight=0.3),
            ],
        )],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["zip"])]),
        identity=IdentityConfig(
            enabled=True, path=db, source_pk_column="id",
            dataset="conflict-e2e", weak_confidence_threshold=0.9,
        ),
    )
    result = run_dedupe_df(df, cfg, source_name="src")
    assert result["identity_summary"] is not None
    # With a very strict threshold (0.9) at least one cluster is "weak" and
    # gets a conflict flag.
    assert result["identity_summary"]["conflicts_flagged"] >= 1
    with IdentityStore(path=db) as s:
        assert len(s.find_conflicts(dataset="conflict-e2e")) >= 1
