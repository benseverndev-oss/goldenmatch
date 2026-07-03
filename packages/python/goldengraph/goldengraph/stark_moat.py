"""SP-moat: turn a resolver's clusters into cluster-ordinal ids that drive BOTH
materializations (index + store), plus the int canon scoring map. The clustering is
the single ER lever; the store's overlap-merge is NOT used (a batch reconciles only
against already-stored entities, so same-batch aliases would never merge -- we
collapse in Python instead). See the spec.
"""
from __future__ import annotations


def build_clusters(canon, method_clusters, all_ids):
    """`canon`: alias_id -> original id (STRING stark ids). `method_clusters`: resolver
    output over the INJECTED aliases (list[list[alias_id]]). `all_ids`: every node id in
    the injected graph (aliases + passthrough non-targets). Returns (ordinal_of, ord2canon):
      ordinal_of: id -> cluster_ordinal (int, deterministic)
      ord2canon:  cluster_ordinal -> canonical original id, **as INT** (STaRK node ids
                  are integers; the int match is what makes scoring against the int
                  `gold` sets work -- a str value here scores ~0 for every method).
    Injected aliases group by `method_clusters`; every other id is its own singleton
    cluster. Ordinals assigned in sorted order for determinism."""
    ordinal_of: dict[str, int] = {}
    ord2canon: dict[int, int] = {}
    clustered: set[str] = set()
    # deterministic order: sort clusters by their lexicographically smallest member
    ordered = sorted((sorted(c) for c in method_clusters), key=lambda c: c[0])
    ordinal = 0
    for members in ordered:
        for m in members:
            ordinal_of[m] = ordinal
            clustered.add(m)
        # canonical = the original the cluster's members map to (first by sort); a
        # resolver error mixing two originals deterministically picks the smallest.
        ord2canon[ordinal] = int(canon.get(members[0], members[0]))
        ordinal += 1
    for nid in sorted(set(all_ids) - clustered):        # singletons (non-targets etc.)
        ordinal_of[nid] = ordinal
        ord2canon[ordinal] = int(canon.get(nid, nid))
        ordinal += 1
    return ordinal_of, ord2canon
