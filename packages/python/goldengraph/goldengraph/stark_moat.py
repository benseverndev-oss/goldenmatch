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


def collapse_for_index(nodes2, node_texts2, ordinal_of):
    """One index entry per cluster: entity_id = ordinal, canonical_name = the joined
    member docs (the MERGED text -- where dense-ER recovery happens), typ from the
    first member. Docs joined in stable (ordinal, id) order."""
    text_of = dict(zip([n[0] for n in nodes2], node_texts2))
    typ_of = {n[0]: n[2] for n in nodes2}
    members: dict[int, list[str]] = {}
    for nid, _name, _typ in nodes2:
        members.setdefault(ordinal_of[nid], []).append(nid)
    out = []
    for ordv, ids in sorted(members.items()):
        ids_sorted = sorted(ids)
        doc = " ".join(text_of.get(i, "") for i in ids_sorted).strip()
        out.append({"entity_id": ordv, "canonical_name": doc, "typ": typ_of[ids_sorted[0]]})
    return out


def collapse_for_store(nodes2, edges2, ordinal_of):
    """One store node per cluster (id = str(ordinal)) + edges remapped endpoint ->
    its cluster ordinal, dropping intra-cluster self-loops. Feed to bulk_load
    UNCHANGED -- each node's unique key = str(ordinal) => passthrough, so the store
    holds exactly this pre-merged graph (no store-side merge)."""
    seen: dict[int, tuple] = {}
    for nid, name, typ in nodes2:
        ordv = ordinal_of[nid]
        seen.setdefault(ordv, (str(ordv), name, typ))       # first member names the node
    coll_nodes = [seen[o] for o in sorted(seen)]
    coll_edges = []
    for s, p, o in edges2:
        so, oo = ordinal_of[s], ordinal_of[o]
        if so == oo:
            continue                                        # intra-cluster self-loop
        coll_edges.append((str(so), p, str(oo)))
    return coll_nodes, coll_edges
