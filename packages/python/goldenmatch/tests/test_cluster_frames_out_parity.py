"""SP-A: build_cluster_frames(...) -> ClusterFrames, gated
GOLDENMATCH_CLUSTER_FRAMES_OUT. cluster_frames_to_dict(frames) must round-trip to
build_clusters gate-ON (the score-free dict): members-as-set, pair_scores stripped
(both carry {}), EVERYTHING ELSE strict byte-identical. Native reads the Arrow kernel
metadata; off-native the transient fill. Native leg SKIPS locally, runs in CI.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.cluster import (
    build_cluster_frames,
    build_clusters,
    cluster_frames_to_dict,
)
from goldenmatch.core.cluster_pairscores import ClusterPairScores


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


# --- Stage-1: ClusterPairScores.from_frames vectorized-join parity --------------
#
# from_frames now builds _by_cid via a Polars join instead of the Python
# _bucket_pairs loop. It MUST stay byte-identical to from_pairs(all_pairs,
# clusters): same kept-pair set, same key insertion order (first-occurrence),
# same value (LAST-occurrence score), reversed keys distinct, self-pairs kept,
# cross-cut/absent endpoints dropped. The fixture below encodes every one of
# those invariants by construction so the parity gate is a real verifier.


def _stage1_join_fixture():
    """Tiny hand-verifiable fixture for the from_frames join.

    Final cids (a cascading auto-split shape: a parent cluster that split into
    children and a child that re-split, modeled directly as multiple distinct
    final cids that share a pre-split member id space):

      cid 10: members {1, 2, 3, 7}
      cid 20: members {4, 5}
      cid 30: members {9}          (singleton, no kept pair)

    Raw pairs (index : row):
      0  (1, 2, 0.90)   kept@10 ; first occurrence of key (1,2)
      1  (1, 2, 0.40)   kept@10 ; LATER dup, LOWER score -> LAST-WINS=0.40
                                  (MAX would wrongly give 0.90 -> catches .max())
      2  (7, 3, 0.70)   kept@10 ; key (7,3)
      3  (3, 7, 0.80)   kept@10 ; key (3,7) -- DISTINCT from (7,3), NOT canonicalized
      4  (1, 1, 0.55)   kept@10 ; self-pair, 1 in cid -> KEPT
      5  (2, 4, 0.60)   DROPPED ; cross-cut (2 in cid10, 4 in cid20)
      6  (4, 5, 0.95)   kept@20
      7  (5, 99, 0.30)  DROPPED ; endpoint 99 absent from assignments

    Expected (byte-identical to from_pairs), key order = first occurrence:
      cid 10: {(1,2): 0.40, (7,3): 0.70, (3,7): 0.80, (1,1): 0.55}
      cid 20: {(4,5): 0.95}
      cid 30: {}   (singleton, no kept pair)
    """
    pairs = [
        (1, 2, 0.90),
        (1, 2, 0.40),
        (7, 3, 0.70),
        (3, 7, 0.80),
        (1, 1, 0.55),
        (2, 4, 0.60),
        (4, 5, 0.95),
        (5, 99, 0.30),
    ]
    clusters = {
        10: {"members": [1, 2, 3, 7], "size": 4, "pair_scores": {}},
        20: {"members": [4, 5], "size": 2, "pair_scores": {}},
        30: {"members": [9], "size": 1, "pair_scores": {}},
    }
    # one row per (cluster_id, member_id); singletons included
    assignments = pl.DataFrame(
        {
            "cluster_id": [10, 10, 10, 10, 20, 20, 30],
            "member_id": [1, 2, 3, 7, 4, 5, 9],
        }
    )
    return pairs, clusters, assignments


def test_from_frames_join_byte_identical_to_from_pairs():
    pairs, clusters, assignments = _stage1_join_fixture()

    # Join fan-out guard: a duplicated member_id would silently multiply rows.
    assert assignments["member_id"].is_unique().all()

    v_frames = ClusterPairScores.from_frames(assignments, pairs)
    v_pairs = ClusterPairScores.from_pairs(pairs, clusters)

    # EXACT per-cid parity: keys, key ORDER, and values.
    for cid in clusters:
        got = list(v_frames.for_cluster(cid).items())
        ref = list(v_pairs.for_cluster(cid).items())
        assert got == ref, f"cid {cid}: from_frames={got} from_pairs={ref}"

    # iter_clusters parity (cid order + emitted rows).
    assert list(v_frames.iter_clusters()) == list(v_pairs.iter_clusters())
