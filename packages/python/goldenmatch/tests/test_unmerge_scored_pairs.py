"""unmerge_record optional scored_pairs source (decouple from cluster pair_scores).

Re-clustering from an explicit scored_pairs list (filtered to the affected
cluster's members) is byte-identical to reading cluster["pair_scores"] -- for a
single cluster the member-filter excludes cross-cut edges, so the two sources
carry the same within-member edge set. Lets unmerge survive a future build that
drops pair_scores from the returned dict.
"""
import copy

from goldenmatch.core.cluster import unmerge_record


def _clusters():
    # cid 1: chain {0,1,2} held by 0-1 (0.9) and 1-2 (0.5); removing 1 splits it.
    # cid 2: pair {3,4}. cid 3: singleton {5}.
    return {
        1: {"members": [0, 1, 2], "size": 3, "oversized": False,
            "pair_scores": {(0, 1): 0.9, (1, 2): 0.5}, "confidence": 0.7,
            "bottleneck_pair": (1, 2), "cluster_quality": "weak"},
        2: {"members": [3, 4], "size": 2, "oversized": False,
            "pair_scores": {(3, 4): 0.95}, "confidence": 0.95,
            "bottleneck_pair": None, "cluster_quality": "strong"},
        3: {"members": [5], "size": 1, "oversized": False,
            "pair_scores": {}, "confidence": 1.0,
            "bottleneck_pair": None, "cluster_quality": "strong"},
    }


def _flat(clusters):
    out = []
    for c in clusters.values():
        for (a, b), s in c["pair_scores"].items():
            out.append((a, b, s))
    return out


def test_scored_pairs_matches_dict_path_for_each_record():
    base = _clusters()
    flat = _flat(base)
    for rid in [0, 1, 2, 3, 4, 5, 99]:
        from_dict = unmerge_record(rid, copy.deepcopy(base))
        from_flat = unmerge_record(rid, copy.deepcopy(base), scored_pairs=flat)
        assert from_flat == from_dict, f"record {rid}: {from_flat} != {from_dict}"


def test_recluster_from_scored_pairs_without_dict_pair_scores():
    # Simulate the future build-drop: strip pair_scores; supply scored_pairs.
    flat = _flat(_clusters())
    stripped = copy.deepcopy(_clusters())
    for c in stripped.values():
        c.pop("pair_scores", None)
    out = unmerge_record(1, stripped, scored_pairs=flat)
    # Removing 1 from {0,1,2} (edges 0-1, 1-2 both touch 1) leaves no edges among
    # {0,2} -> each a singleton, plus 1 as a singleton.
    members = sorted(sorted(c["members"]) for c in out.values())
    assert [0] in members and [1] in members and [2] in members


def test_none_path_tolerant_when_pair_scores_absent():
    # None path on a pair_scores-less dict degrades (no KeyError) instead of crashing.
    stripped = _clusters()
    stripped[1].pop("pair_scores", None)
    out = unmerge_record(1, stripped)  # no scored_pairs
    assert out is not None


def test_engine_unmerge_passes_scored_pairs(monkeypatch):
    import goldenmatch.core.cluster as _cluster
    import polars as pl
    from goldenmatch.tui.engine import EngineResult, MatchEngine

    base = _clusters()
    flat = _flat(base)
    eng = MatchEngine.__new__(MatchEngine)  # bypass file-loading __init__
    eng._data = pl.DataFrame({"x": list(range(6))})
    eng._last_result = EngineResult(
        clusters=copy.deepcopy(base), golden=None, unique=None, dupes=None,
        quarantine=None, matched=None, unmatched=None, scored_pairs=flat, stats={},
    )

    captured = {}
    real = _cluster.unmerge_record

    def _spy(*args, **kwargs):
        captured["scored_pairs"] = kwargs.get("scored_pairs")
        return real(*args, **kwargs)

    monkeypatch.setattr(_cluster, "unmerge_record", _spy)
    eng.unmerge_record(1)
    assert captured["scored_pairs"] == flat
