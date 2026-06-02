"""SP-A: build_cluster_frames(...) -> ClusterFrames, gated
GOLDENMATCH_CLUSTER_FRAMES_OUT. cluster_frames_to_dict(frames) must round-trip to
build_clusters gate-ON (the score-free dict): members-as-set, pair_scores stripped
(both carry {}), EVERYTHING ELSE strict byte-identical. Native reads the Arrow kernel
metadata; off-native the transient fill. Native leg SKIPS locally, runs in CI.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.cluster import (
    build_cluster_frames,
    build_clusters,
    cluster_frames_to_dict,
)


def _norm(cinfo: dict) -> dict:
    out = {k: v for k, v in cinfo.items()
           if k not in ("members", "pair_scores", "_was_split")}
    out["members"] = frozenset(cinfo["members"])
    return out


def _no_split_pairs():
    # singleton id 0, {1,2}, fully-connected {3,4,5}, weak chain {6,7,8}.
    # max_cluster_size=5 => nothing oversized => Step-2 split path NOT exercised.
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (6, 7, 0.99), (7, 8, 0.40),
    ]
    all_ids = list(range(0, 9))
    return pairs, all_ids


def _skip_if_no_native(native):
    if native == "1":
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        if nm is None or getattr(nm, "build_clusters_arrow", None) is None:
            pytest.skip("native cluster kernel absent; native=1 validated in CI")


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_out_roundtrips_to_dict_no_split(monkeypatch, native):
    pairs, all_ids = _no_split_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    _skip_if_no_native(native)

    kw = dict(all_ids=all_ids, max_cluster_size=5,
              weak_cluster_threshold=0.3, auto_split=True)

    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")  # score-free dict
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", raising=False)
    ref = build_clusters(pairs, **kw)

    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    frames = build_cluster_frames(pairs, **kw)
    got = cluster_frames_to_dict(frames)

    assert got.keys() == ref.keys()
    for cid in ref:
        assert _norm(got[cid]) == _norm(ref[cid]), (
            f"cluster {cid}:\n got={got[cid]}\n ref={ref[cid]}"
        )
