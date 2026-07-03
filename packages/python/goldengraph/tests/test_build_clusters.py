"""SP-moat cluster assembly + INT canon scoring map (pure). ord2canon values are
INTS -- the original STaRK node ids -- so they match the int `gold` sets in
stark_metrics. (A str->int mismatch here would make every method score ~0.)"""
from __future__ import annotations

from goldengraph.stark_moat import build_clusters


def test_build_clusters_targets_grouped_nontargets_singleton():
    canon = {"1#a0": "1", "1#a1": "1", "2": "2"}
    method_clusters = [["1#a0", "1#a1"]]               # resolver merged the two aliases
    all_ids = ["1#a0", "1#a1", "2"]
    ordinal_of, ord2canon = build_clusters(canon, method_clusters, all_ids)
    assert ordinal_of["1#a0"] == ordinal_of["1#a1"]    # aliases share an ordinal
    assert ordinal_of["2"] != ordinal_of["1#a0"]        # non-target its own ordinal
    assert ord2canon[ordinal_of["1#a0"]] == 1           # INT canonical original (matches int gold)
    assert ord2canon[ordinal_of["2"]] == 2
    assert all(isinstance(v, int) for v in ord2canon.values())


def test_build_clusters_fragmented_all_singletons():
    canon = {"1#a0": "1", "1#a1": "1"}
    ordinal_of, ord2canon = build_clusters(canon, [["1#a0"], ["1#a1"]], ["1#a0", "1#a1"])
    assert ordinal_of["1#a0"] != ordinal_of["1#a1"]     # fragmented -> distinct ordinals
    assert ord2canon[ordinal_of["1#a0"]] == 1           # both still map to original 1 (int)
