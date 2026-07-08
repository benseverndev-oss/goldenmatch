"""Pure IR + lower — the SP2 mirror of goldenpipe-core/src/ir.rs. Deterministic,
no I/O. `lower` turns a captured concrete config into IR nodes with sequential ids."""
from __future__ import annotations


def _node(kind, nid, origin, resolved, **rest):
    return {"kind": kind, "id": nid, "origin_stage": origin, "resolved": resolved, **rest}


def lower(origin_stage: str, kind_hint: str, concrete: dict, next_id: int, resolved: bool = False):
    """(origin_stage, kind_hint, concrete_config, next_id) -> (nodes, next_id).
    kind_hint routes to the node builder; unknown -> Barrier. Pure + total."""
    nid = next_id
    nodes = []
    if kind_hint == "source":
        nodes.append(_node("Source", nid, origin_stage, resolved, produces=["df"]))
        nid += 1
    elif kind_hint == "scan":
        for col in concrete.get("columns", []):
            nodes.append(_node("Scan", nid, origin_stage, resolved, column=col["column"], ops=list(col.get("ops", []))))
            nid += 1
    elif kind_hint == "map":
        for spec in concrete.get("transforms", []):
            for op in spec.get("ops", []):
                nodes.append(_node("Map", nid, origin_stage, resolved, column=spec["column"], op=op))
                nid += 1
    elif kind_hint == "match":
        nodes.append(_node("Partition", nid, origin_stage, resolved, keys=list(concrete.get("keys", []))))
        nid += 1
        nodes.append(_node("PairScore", nid, origin_stage, resolved, scorer=concrete.get("scorer")))
        nid += 1
        nodes.append(_node("Connected", nid, origin_stage, resolved, method=concrete.get("method")))
        nid += 1
    else:  # barrier / unknown
        nodes.append(_node("Barrier", nid, origin_stage, resolved, raw_config=concrete))
        nid += 1
    return nodes, nid
