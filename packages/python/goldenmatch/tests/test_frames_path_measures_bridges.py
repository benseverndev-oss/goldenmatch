"""The frames-path cluster emitter must MEASURE bridges, not hardcode zero.

`_emit_cluster_profile_frames` wrote `bridge_edge_count=0, measured_bridge_risk=0.0`
unconditionally while holding everything the dict path measures from. Two
consequences, both measured on Abt-Buy:

1. The frames emitter runs LAST (dict=1 call, frames=4), so its zero CLOBBERS the
   dict path's real value. The controller's per-iteration profiles reported
   bridge_edge_count=0 on every iteration that did not split, and 59 on the one
   that did -- so a rule predicting "splitting drives bridges DOWN" verified its
   own prediction against 0 -> 59 and read its own success as a failure.

2. `zero_label_confidence` branches on `measured_bridge_risk is not None` and
   falls back to a heuristic proxy when it is None. A hardcoded 0.0 is not None,
   so the frames path asserted "measured: no bridge risk" for a quantity it never
   measured, and suppressed the fallback.

The invariant these tests pin is agreement, not a magic number: both emitters see
the same clustering, so they must report the same bridges. A fixture whose true
count is hardcoded would just re-encode today's `_severe_bridge_count`.
"""

from __future__ import annotations

import polars as pl
from goldenmatch.core.cluster import (
    _emit_cluster_profile,
    _emit_cluster_profile_frames,
)
from goldenmatch.core.frame import to_frame
from goldenmatch.core.profile_emitter import profile_capture


def _two_cliques_joined_by_one_weak_edge() -> tuple[dict, tuple]:
    """{1,2,3} and {4,5,6} fully connected, joined only by the 3-4 edge.

    Removing 3-4 leaves two sides of 3 each -- both >= 2 -- so it is a SEVERE
    bridge by `_severe_bridge_count`'s definition, and the one edge in the
    cluster that is.
    """
    members = [1, 2, 3, 4, 5, 6]
    pair_scores = {
        (1, 2): 0.95, (1, 3): 0.95, (2, 3): 0.95,
        (4, 5): 0.95, (4, 6): 0.95, (5, 6): 0.95,
        (3, 4): 0.62,  # the weak bridge
    }
    clusters = {
        1: {"members": members, "size": len(members),
            "oversized": False, "confidence": 0.9, "pair_scores": pair_scores},
    }
    return clusters, (members, pair_scores)


def _frames_inputs(clusters: dict):
    """Build the metadata/assignments/pairs frames the frames emitter consumes."""
    metadata = pl.DataFrame([
        {"cluster_id": cid, "size": c["size"], "oversized": c["oversized"],
         "confidence": c["confidence"]}
        for cid, c in clusters.items()
    ])
    assignments = pl.DataFrame([
        {"cluster_id": cid, "member_id": m}
        for cid, c in clusters.items() for m in c["members"]
    ])
    pairs = [
        (a, b, s)
        for c in clusters.values()
        for (a, b), s in c["pair_scores"].items()
    ]
    return to_frame(metadata), to_frame(assignments), pairs


def _capture(fn) -> object:
    with profile_capture() as emitter:
        fn()
        return emitter.cluster


def test_the_two_emitters_agree_on_bridge_count():
    clusters, _ = _two_cliques_joined_by_one_weak_edge()
    md, asg, pairs = _frames_inputs(clusters)

    from_dict = _capture(lambda: _emit_cluster_profile(clusters))
    from_frames = _capture(lambda: _emit_cluster_profile_frames(md, asg, pairs))

    assert from_dict.bridge_edge_count == from_frames.bridge_edge_count, (
        f"dict path says {from_dict.bridge_edge_count} bridges, frames path says "
        f"{from_frames.bridge_edge_count}, on identical clusters"
    )
    assert from_dict.bridge_edge_count > 0, (
        "fixture is not exercising the code -- it has no severe bridge, so both "
        "paths would agree at 0 whether or not the frames path measures anything"
    )


def test_the_two_emitters_agree_on_measured_risk():
    clusters, _ = _two_cliques_joined_by_one_weak_edge()
    md, asg, pairs = _frames_inputs(clusters)

    from_dict = _capture(lambda: _emit_cluster_profile(clusters))
    from_frames = _capture(lambda: _emit_cluster_profile_frames(md, asg, pairs))

    assert from_dict.measured_bridge_risk == from_frames.measured_bridge_risk
    assert from_frames.measured_bridge_risk == 1.0, (
        "one measurable cluster, and it is risky -- risk is risky/measurable"
    )


def test_unmeasurable_clusters_report_none_not_zero():
    """`None` means 'not measured' and routes zero_label to its heuristic proxy;
    `0.0` asserts a measurement. Singletons are not measurable, so the frames
    path must say None -- the exact distinction the hardcoded 0.0 destroyed."""
    clusters = {
        cid: {"members": [cid], "size": 1, "oversized": False,
              "confidence": 0.9, "pair_scores": {}}
        for cid in (1, 2, 3)
    }
    md, asg, pairs = _frames_inputs(clusters)

    from_dict = _capture(lambda: _emit_cluster_profile(clusters))
    from_frames = _capture(lambda: _emit_cluster_profile_frames(md, asg, pairs))

    assert from_dict.measured_bridge_risk is None
    assert from_frames.measured_bridge_risk is None, (
        "hardcoded 0.0 would claim a measurement that never happened"
    )
    assert from_frames.bridge_edge_count == 0


def test_a_clique_with_no_weak_link_has_no_bridges():
    """Guards the opposite error: measuring bridges everywhere. A fully
    connected cluster has no edge whose removal disconnects it."""
    members = [1, 2, 3, 4]
    pair_scores = {(a, b): 0.9 for a in members for b in members if a < b}
    clusters = {1: {"members": members, "size": 4, "oversized": False,
                    "confidence": 0.9, "pair_scores": pair_scores}}
    md, asg, pairs = _frames_inputs(clusters)

    from_frames = _capture(lambda: _emit_cluster_profile_frames(md, asg, pairs))
    assert from_frames.bridge_edge_count == 0
    assert from_frames.measured_bridge_risk == 0.0


def test_no_edge_list_reports_unmeasured_not_zero_risk():
    """`pairs` defaults to None. Without edges nothing is measurable, and the
    honest answer is None -- a 0.0 would claim the clusters carry no bridge
    risk when in fact nobody looked."""
    clusters, _ = _two_cliques_joined_by_one_weak_edge()
    md, asg, _ = _frames_inputs(clusters)

    from_frames = _capture(lambda: _emit_cluster_profile_frames(md, asg, None))
    assert from_frames.measured_bridge_risk is None
    assert from_frames.bridge_edge_count == 0


def test_dense_clusters_are_skipped_rather_than_measured_expensively():
    """`_severe_bridge_count` is O(E x V) per cluster -- one BFS per edge -- and
    the cluster-size cap bounds V but not E. Extending the measurement to the
    frames path put that on the arrow-native default lane and took
    `python_goldenmatch_heavy` shard 3 from ~115s to over its 300s timeout.

    Measured on 40 x 100-member cliques (198,000 edges): 129.288s unbounded
    versus 0.067s with the edge budget.

    Note what the slow path returned: (0, 0.0). A fully connected cluster has no
    cut edge, so the most expensive shape is also the least informative one. The
    budget declines it and says `None` -- "not measured" -- rather than claiming
    the zero it would have found.
    """
    from goldenmatch.core.cluster import (
        _BRIDGE_MAX_EDGES_PER_CLUSTER,
        _measure_bridges_frames,
    )

    members = list(range(60))
    edges = {(a, b): 0.9 for i, a in enumerate(members) for b in members[i + 1:]}
    assert len(edges) > _BRIDGE_MAX_EDGES_PER_CLUSTER, "fixture must exceed the budget"
    member_to_cid = dict.fromkeys(members, 1)

    bridges, risk = _measure_bridges_frames({1: members}, edges, member_to_cid)
    assert bridges == 0
    assert risk is None, (
        "an over-budget cluster is EXCLUDED from `measurable`, so nothing was "
        "measured and the honest report is None, not 0.0"
    )


def test_a_sparse_cluster_under_budget_is_still_measured():
    """The budget must not silently switch measurement off for ordinary
    clusters -- a chain of 60 nodes carries 59 edges, far under the bar."""
    from goldenmatch.core.cluster import _measure_bridges_frames

    members = list(range(60))
    edges = {(a, a + 1): 0.9 for a in range(59)}
    member_to_cid = dict.fromkeys(members, 1)

    bridges, risk = _measure_bridges_frames({1: members}, edges, member_to_cid)
    assert risk == 1.0, "measured, and the cluster is one long chain of bridges"
    assert bridges > 0
