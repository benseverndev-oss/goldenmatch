"""Byte-identical parity: ``GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1`` (columnar
cluster-build core via ``_build_clusters_via_frames``) must produce a
``dict[int, dict]`` that is IDENTICAL to the default dict path -- key for key,
under BOTH ``GOLDENMATCH_NATIVE`` states.

This is the durability gate for SP1 of the Arrow-native columnar-cluster
roadmap. The columnar path feeds golden-record + identity-graph durability, so
parity is the hard invariant. ``members`` is compared as a SET (the columnar
path runs a SEPARATE Union-Find, so list order legitimately differs -- PR #598
removed sorting); EVERYTHING ELSE (pair_scores, confidence EXACT float,
bottleneck_pair, cluster_quality, oversized, cluster ids) is STRICT
byte-identical.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.cluster import build_clusters


def _adversarial_pairs():
    # Cluster A: singleton id 0 (no pairs). Cluster B: 2-member {1,2}.
    # Cluster C: fully-connected {3,4,5}. Cluster D: weak chain {6,7,8}
    # (one weak edge -> triggers weak). Cluster E: oversized that SPLITS
    # (a barbell: two dense triangles joined by one weak bridge, > max_cluster_size=5).
    # Cluster F: score-tied edges (bottleneck tie-break). Plus duplicate canonical pair.
    # Cluster G: DENSE oversized clique {30..36} all at 0.99 (no weak bridge) ->
    # split_oversized_cluster returns it unchanged -> stays oversized, exercises
    # the no-progress guard.
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (6, 7, 0.99), (7, 8, 0.40),                 # weak: avg-min large
        # barbell oversized (ids 10..16, 7 members > max 5):
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (14, 15, 0.99), (15, 16, 0.99), (14, 16, 0.99),
        (12, 14, 0.31),                              # weak bridge -> splits
        (20, 21, 0.5), (20, 22, 0.5),                # score ties -> bottleneck first-occurrence
        (1, 2, 0.95),                                # duplicate canonical pair
        # dense oversized clique 30..36 (7 members > max 5), all 0.99, no weak
        # bridge -> can't split -> stays oversized (no-progress guard):
        (30, 31, 0.99), (30, 32, 0.99), (30, 33, 0.99), (30, 34, 0.99),
        (30, 35, 0.99), (30, 36, 0.99), (31, 32, 0.99), (31, 33, 0.99),
        (31, 34, 0.99), (31, 35, 0.99), (31, 36, 0.99), (32, 33, 0.99),
        (32, 34, 0.99), (32, 35, 0.99), (32, 36, 0.99), (33, 34, 0.99),
        (33, 35, 0.99), (33, 36, 0.99), (34, 35, 0.99), (34, 36, 0.99),
        (35, 36, 0.99),
    ]
    all_ids = list(range(0, 23)) + list(range(30, 37))
    return pairs, all_ids


def _norm(cinfo: dict) -> dict:
    out = {k: v for k, v in cinfo.items() if k not in ("members", "_was_split")}
    out["members"] = frozenset(cinfo["members"])
    return out


@pytest.mark.parametrize("native", ["1", "0"])
def test_columnar_build_byte_identical(monkeypatch, native):
    import goldenmatch.core.cluster as _cmod
    pairs, all_ids = _adversarial_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)

    if native == "1":
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        if nm is None or getattr(nm, "build_clusters_arrow", None) is None:
            pytest.skip(
                "native cluster kernel (build_clusters_arrow/mst_split_components) "
                "absent in this environment; native=1 parity is validated in CI's "
                "fresh native build"
            )

    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "0")
    off = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                         weak_cluster_threshold=0.3, auto_split=True)

    calls = []
    real = _cmod._build_clusters_via_frames
    monkeypatch.setattr(_cmod, "_build_clusters_via_frames",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")
    on = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                        weak_cluster_threshold=0.3, auto_split=True)
    assert calls, "columnar path (_build_clusters_via_frames) did not run with gate ON"

    assert on.keys() == off.keys()
    for cid in off:
        assert _norm(on[cid]) == _norm(off[cid]), f"cluster {cid} differs:\n on={on[cid]}\n off={off[cid]}"
