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


def _adversarial_pairs():
    # Mirror tests/test_columnar_drop_pairscores_parity.py::_adversarial_pairs:
    # singleton id 0, {1,2}, fully-connected {3,4,5}, weak chain {6,7,8}, barbell
    # oversized that splits (10..16 minus 13), score-tied (20,21,22), dup pair,
    # dense clique that can't cleanly split (30..36). PLUS a different-score
    # duplicate canonical pair (3,4) to exercise the kernel last-wins dedup.
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (3, 4, 0.91),                               # different-score dup (last-wins)
        (6, 7, 0.99), (7, 8, 0.40),
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (14, 15, 0.99), (15, 16, 0.99), (14, 16, 0.99),
        (12, 14, 0.31),
        (20, 21, 0.5), (20, 22, 0.5),
        (1, 2, 0.95),                               # same-score dup
        (30, 31, 0.99), (30, 32, 0.99), (30, 33, 0.99), (30, 34, 0.99),
        (30, 35, 0.99), (30, 36, 0.99), (31, 32, 0.99), (31, 33, 0.99),
        (31, 34, 0.99), (31, 35, 0.99), (31, 36, 0.99), (32, 33, 0.99),
        (32, 34, 0.99), (32, 35, 0.99), (32, 36, 0.99), (33, 34, 0.99),
        (33, 35, 0.99), (33, 36, 0.99), (34, 35, 0.99), (34, 36, 0.99),
        (35, 36, 0.99),
    ]
    all_ids = list(range(0, 23)) + list(range(30, 37))
    return pairs, all_ids


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


@pytest.mark.parametrize("native", ["1", "0"])
def test_frames_out_roundtrips_to_dict_full(monkeypatch, native):
    pairs, all_ids = _adversarial_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    _skip_if_no_native(native)
    kw = dict(all_ids=all_ids, max_cluster_size=5,
              weak_cluster_threshold=0.3, auto_split=True)
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", raising=False)
    ref = build_clusters(pairs, **kw)
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    got = cluster_frames_to_dict(build_cluster_frames(pairs, **kw))
    # DIAGNOSTIC (temporary): surface exactly how the cid sets / partitions differ.
    if got.keys() != ref.keys():
        ref_part = {frozenset(c["members"]) for c in ref.values()}
        got_part = {frozenset(c["members"]) for c in got.values()}
        print("ONLY-IN-GOT cids:", sorted(set(got) - set(ref)))
        print("ONLY-IN-REF cids:", sorted(set(ref) - set(got)))
        print("PARTITIONS EQUAL (label-only divergence)?", ref_part == got_part)
        print("ONLY-IN-GOT partition:", sorted(map(sorted, got_part - ref_part)))
        print("ONLY-IN-REF partition:", sorted(map(sorted, ref_part - got_part)))
        print("REF  cid->members:", {k: sorted(v["members"]) for k, v in sorted(ref.items())})
        print("GOT  cid->members:", {k: sorted(v["members"]) for k, v in sorted(got.items())})
    assert got.keys() == ref.keys()
    for cid in ref:
        assert _norm(got[cid]) == _norm(ref[cid]), f"cid {cid}: {got[cid]} vs {ref[cid]}"


def test_step3_quality_matches_dict_loop(monkeypatch):
    # Seam test: vectorized weak/quality == dict loop per-row, native=0.
    pairs, all_ids = _adversarial_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    kw = dict(all_ids=all_ids, max_cluster_size=5,
              weak_cluster_threshold=0.3, auto_split=True)
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")
    ref = build_clusters(pairs, **kw)
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "1")
    got = cluster_frames_to_dict(build_cluster_frames(pairs, **kw))
    for cid in ref:
        assert got[cid]["cluster_quality"] == ref[cid]["cluster_quality"]
        assert got[cid]["confidence"] == ref[cid]["confidence"]  # EXACT
