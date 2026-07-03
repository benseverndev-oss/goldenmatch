"""STaRK KB adapter: download a STaRK semi-structured KB from HuggingFace and map
it to goldengraph's ``(nodes, edges)`` + a query set. Integration surface -- the
pure metrics live in ``stark_metrics.py`` (box-tested); this module is exercised by
the Modal feasibility run, not the box suite.

STaRK API (snap-stanford/stark, ``stark_qa`` package):
- ``skb = load_skb(name)``: node ids are ``0..len(skb)-1``; ``skb.node_info[nid]``
  is a dict ``{'id','type','name'|'title','source',...}``; ``skb.get_node_type_by_id(nid)``
  gives the type string; edges are ``skb.edge_index`` (2xE LongTensor) +
  ``skb.edge_types`` (E LongTensor), decoded via ``skb.get_edge_type_by_id(tid)``.
- ``qa = load_qa(name)``: ``qa.get_idx_split()`` -> {'train','val','test',...} of
  LongTensor indices; ``qa[idx]`` -> ``(query, q_id, answer_ids, meta)`` with
  ``answer_ids`` a python list of ints.

See docs/superpowers/specs/2026-07-02-goldengraph-stark-bulkload-design.md.
"""
from __future__ import annotations

import time


def _node_name(info: dict) -> str:
    # PRIME/MAG use 'name'; AMAZON uses 'title'. Fall back through both.
    return str(info.get("name") or info.get("title") or "").strip()


def load_stark_kb(name: str, *, split: str = "test", limit_queries: int | None = None):
    """Return ``(nodes, edges, queries)`` for a STaRK KB.

    nodes: list of ``(stark_id: str, name: str, typ: str)``.
    edges: list of ``(subj_stark_id: str, predicate: str, obj_stark_id: str)``.
    queries: list of ``(query_text: str, gold_stark_ids: set[int])`` from ``split``.

    Node/gold ids stay INTS for retrieval+scoring; ids are stringified only for the
    store's opaque ``record_keys`` (done inside ``bulk_load``)."""
    # stark_qa is installed --no-deps (its FULL tree pulls the colbert/gritlm/mteb retrieval
    # baselines -- what we replace -- and pip can't resolve them). Its package __init__ imports
    # load_model -> stark_qa.models -> those baselines at top level, so seed lightweight MagicMock
    # stand-ins for the heavy backends: we only call the SKB/QA DATA loaders, never a model.
    import sys
    from unittest.mock import MagicMock

    for _m in ("colbert", "colbert.infra", "colbert.infra.config", "colbert.infra.run",
               "colbert.data", "colbert.modeling", "colbert.modeling.checkpoint",
               "colbert.searcher", "gritlm", "mteb", "sentence_transformers", "transformers",
               "rank_bm25"):
        sys.modules.setdefault(_m, MagicMock())
    from stark_qa import load_qa, load_skb

    skb = load_skb(name)
    n_nodes = len(skb)
    nodes = []
    for nid in range(n_nodes):
        info = skb.node_info[nid]
        nodes.append((str(nid), _node_name(info), str(skb.get_node_type_by_id(nid))))

    # Real edges live in edge_index (2xE) + edge_types (E); get_tuples() is only the
    # SCHEMA (type-triples), not instances. Materialize once as python lists (fast),
    # decode each relation-type id once via a cache.
    ei = skb.edge_index.tolist()  # [[heads...], [tails...]]
    et = skb.edge_types.tolist()
    rel_cache: dict[int, str] = {}

    def _rel(tid: int) -> str:
        r = rel_cache.get(tid)
        if r is None:
            r = str(skb.get_edge_type_by_id(tid))
            rel_cache[tid] = r
        return r

    heads, tails = ei[0], ei[1]
    edges = [(str(heads[i]), _rel(et[i]), str(tails[i])) for i in range(len(heads))]

    qa = load_qa(name)
    idx = qa.get_idx_split()[split].tolist()
    if limit_queries:
        idx = idx[:limit_queries]
    queries = []
    for i in idx:
        query, _q_id, answer_ids, _meta = qa[i]
        queries.append((query, {int(a) for a in answer_ids}))
    return nodes, edges, queries


def evaluate(index, slice_graph, stark_to_eid, eid_to_stark, queries, embedder, *,
             arm: str, sample: int | None = None) -> dict:
    """Run one retrieval arm over ``queries``, return mean metrics + timing. ``arm``
    in {"dense","graph"}. The index returns STARK ids (entity_id=int(stark_id) at
    build time), so Arm A needs no translation and covers ALL nodes. Arm B walks the
    STORE (``as_of().query`` -- the thing under test), translating stark<->slice-local
    ids only at the walk boundary. ``stark_to_eid``/``eid_to_stark`` cover edge-endpoint
    nodes only, which is exactly the set that has neighbors."""
    from erkgbench.stark_metrics import dedup_first_seen, mean_metrics, metrics

    qs = queries[:sample] if sample else queries
    per_query, latencies = [], []
    for text, gold in qs:
        t0 = time.perf_counter()
        if arm == "dense":
            ranked = index.query(text, embedder, k=20)  # stark ids already
        elif arm == "graph":
            seeds = index.query(text, embedder, k=5)  # stark ids
            seed_eids = [stark_to_eid[s] for s in seeds if s in stark_to_eid]
            nbr = [eid_to_stark[e["entity_id"]] for e in _neighbors(slice_graph, seed_eids)]
            ranked = dedup_first_seen([*seeds, *nbr])
        else:
            raise ValueError(f"unknown arm {arm!r}")
        latencies.append(time.perf_counter() - t0)
        per_query.append(metrics(ranked, gold))  # gold: int stark ids
    agg = mean_metrics(per_query)
    lat = sorted(latencies)
    agg["latency_ms_mean"] = 1000 * sum(lat) / (len(lat) or 1)
    agg["latency_ms_p95"] = 1000 * lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
    agg["arm"] = arm
    return agg


def _neighbors(slice_graph, seed_eids):
    """1-hop neighbor entity dicts of ``seed_eids`` (view-local ids) on the slice.
    ``query(ids, 1)`` returns ``{'entities':[...], 'edges':[...]}``; entities are the
    seeds ++ their neighbors, so drop the seeds themselves."""
    if not seed_eids:
        return []
    res = slice_graph.query(list(seed_eids), 1)
    seed_set = set(seed_eids)
    return [e for e in res["entities"] if e["entity_id"] not in seed_set]
