"""#661: split_oversized_cluster_to_size builds the MST once and splits all the
way to max_size, producing the SAME membership partition (and per-component
confidence/bottleneck) as the old repeated single-weakest-edge loop. The
reference below drives the UNCHANGED single-edge split_oversized_cluster, so it
is a genuinely different code path from the batch loop (not tautological)."""
from __future__ import annotations

import pytest
from goldenmatch.core.cluster import (
    split_oversized_cluster,
    split_oversized_cluster_to_size,
)


def _old_partition(members, pair_scores, max_size):
    """Reference: the pre-#661 algorithm. Repeatedly call the unchanged
    single-edge split_oversized_cluster on any still-oversized component,
    re-filtering that component's induced pairs each pass. Returns the FINAL
    list of member-lists. Locks the PARTITION, not labels."""
    work = [list(members)]
    final = []
    while work:
        comp = work.pop()
        if len(comp) <= max_size:
            final.append(comp)
            continue
        ms = set(comp)
        ps = {(a, b): s for (a, b), s in pair_scores.items() if a in ms and b in ms}
        subs = split_oversized_cluster(comp, ps)
        if len(subs) <= 1:
            final.append(comp)        # unsplittable: stays as-is, oversized
            continue
        for sc in subs:
            work.append(sc["members"])
    return final


def _dense_clique(nodes, score=0.99):
    return {(a, b): score for i, a in enumerate(nodes) for b in nodes[i + 1:]}


@pytest.mark.parametrize("native", ["0", "1"])
def test_batch_split_membership_matches_old(monkeypatch, native):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    members = list(range(10, 19))
    ps = {}
    ps.update(_dense_clique([10, 11, 12]))
    ps.update(_dense_clique([13, 14, 15]))
    ps.update(_dense_clique([16, 17, 18]))
    ps[(12, 13)] = 0.30   # weak bridge 1
    ps[(15, 16)] = 0.25   # weak bridge 2
    got = split_oversized_cluster_to_size(members, ps, max_size=2)
    want = _old_partition(members, ps, max_size=2)
    assert {frozenset(s["members"]) for s in got} == {frozenset(c) for c in want}
    from goldenmatch.core.cluster import compute_cluster_confidence
    for s in got:
        ms = set(s["members"])
        induced = {(a, b): v for (a, b), v in ps.items() if a in ms and b in ms}
        ref = compute_cluster_confidence(induced, len(ms))
        assert round(s["confidence"], 12) == round(ref["confidence"], 12)
        assert s["bottleneck_pair"] == ref["bottleneck_pair"]


def test_single_mst_build_per_top_level_cluster(monkeypatch):
    """#661: a dense cluster peeling into k components builds the MST ONCE."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    import goldenmatch.core.cluster as cl
    calls = {"n": 0}
    orig = cl._build_mst
    monkeypatch.setattr(cl, "_build_mst",
                        lambda m, ps: (calls.__setitem__("n", calls["n"] + 1), orig(m, ps))[1])
    members = list(range(100, 130))
    ps = {(a, b): 0.99 for i, a in enumerate(members) for b in members[i + 1:]}
    subs = cl.split_oversized_cluster_to_size(members, ps, max_size=5)
    assert calls["n"] == 1
    assert all(s["size"] <= 5 or s["oversized"] for s in subs)
