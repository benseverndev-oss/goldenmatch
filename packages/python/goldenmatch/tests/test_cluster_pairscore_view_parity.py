"""Byte-identical parity: resolving identity evidence edges via the
``ClusterPairScores`` view (gate ON, ``pair_score_view=`` passed) must emit the
SAME evidence-edge set as the legacy cluster-dict path (gate OFF).

This is the durability gate for Phase 2 SP2: the identity graph (entity-ids +
evidence edges) is the durable layer above run-local clusters, so the edge set
must be byte-identical to the dict path. ``view.for_cluster(cid) ==
clusters[cid]["pair_scores"]`` by construction (the view copies), so any diff is
a wiring bug (wrong gate, wrong loop var, missed a read site), not an algorithm
change.

Entity ids are per-run UUIDs, so the comparison EXCLUDES entity_id and compares
the edge STRUCTURE: (record_a_id, record_b_id, kind, score). Record ids are
deterministic (``src:<id>`` via ``source_pk_col="id"``), so the edge set is
stable across the two resolves.

Parametrized over ``GOLDENMATCH_NATIVE`` in {"1","0"}; native=1 skips when the
native cluster kernel is absent (validated in CI's fresh native build).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.cluster import build_clusters
from goldenmatch.core.cluster_pairscores import ClusterPairScores
from goldenmatch.identity import IdentityStore, resolve_clusters


def _adversarial_pairs():
    # Mirror tests/test_columnar_cluster_build_parity.py::_adversarial_pairs:
    # singleton (id 0), 2-member, fully-connected triple, weak chain, an
    # oversized barbell that SPLITS at max_cluster_size=5, score-tied edges,
    # a duplicate canonical pair, and a dense oversized clique (can't-split).
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (6, 7, 0.99), (7, 8, 0.40),
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (14, 15, 0.99), (15, 16, 0.99), (14, 16, 0.99),
        (12, 14, 0.31),
        (20, 21, 0.5), (20, 22, 0.5),
        (1, 2, 0.95),
        (30, 31, 0.99), (30, 32, 0.99), (30, 33, 0.99), (30, 34, 0.99),
        (30, 35, 0.99), (30, 36, 0.99), (31, 32, 0.99), (31, 33, 0.99),
        (31, 34, 0.99), (31, 35, 0.99), (31, 36, 0.99), (32, 33, 0.99),
        (32, 34, 0.99), (32, 35, 0.99), (32, 36, 0.99), (33, 34, 0.99),
        (33, 35, 0.99), (33, 36, 0.99), (34, 35, 0.99), (34, 36, 0.99),
        (35, 36, 0.99),
    ]
    all_ids = list(range(0, 23)) + list(range(30, 37))
    return pairs, all_ids


def _df(all_ids):
    rows = [{"__row_id__": i, "__source__": "src", "id": str(i), "name": f"n{i}"}
            for i in all_ids]
    return pl.DataFrame(rows)


def _normalized_edges(store: IdentityStore) -> list[tuple]:
    """Read back ALL edges across all identities, normalize to a sorted
    canonical list EXCLUDING entity_id (per-run UUID). Compares the durable
    edge structure: record_a, record_b, kind, score."""
    edges = []
    for node in store.list_identities():
        for e in store.edges_for_entity(node.entity_id):
            edges.append((e.record_a_id, e.record_b_id, e.kind, e.score))
    return sorted(edges, key=lambda t: (t[0], t[1], t[2], -1.0 if t[3] is None else t[3]))


@pytest.mark.parametrize("native", ["1", "0"])
def test_evidence_edges_byte_identical_via_pairscore_view(monkeypatch, tmp_path, native):
    pairs, all_ids = _adversarial_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)

    if native == "1":
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        if nm is None or getattr(nm, "build_clusters_arrow", None) is None:
            pytest.skip(
                "native cluster kernel (build_clusters_arrow) absent in this "
                "environment; native=1 parity is validated in CI's fresh "
                "native build"
            )

    clusters = build_clusters(
        pairs, all_ids=all_ids, max_cluster_size=5,
        weak_cluster_threshold=0.3, auto_split=True,
    )
    df = _df(all_ids)

    # Gate OFF: dict path, fresh store.
    store_off = IdentityStore(path=str(tmp_path / "off.db"))
    try:
        resolve_clusters(
            clusters, df, pairs, "wd", store_off,
            run_name="run-off", source_pk_col="id",
        )
        off_edges = _normalized_edges(store_off)
    finally:
        store_off.close()

    # Gate ON: view path, fresh store. Spy on for_cluster to prove it ran.
    # ClusterPairScores uses __slots__ (no per-instance attrs), so spy via a
    # thin subclass that records each lookup.
    calls: list[int] = []

    class _SpyView(ClusterPairScores):
        def for_cluster(self, cid):
            calls.append(cid)
            return super().for_cluster(cid)

    base = ClusterPairScores.from_cluster_dict(clusters)
    view = _SpyView(base._by_cid)

    store_on = IdentityStore(path=str(tmp_path / "on.db"))
    try:
        resolve_clusters(
            clusters, df, pairs, "wd", store_on,
            run_name="run-on", source_pk_col="id",
            pair_score_view=view,
        )
        on_edges = _normalized_edges(store_on)
    finally:
        store_on.close()

    assert calls, "view.for_cluster never ran with the gate ON (view not wired)"
    assert on_edges == off_edges
