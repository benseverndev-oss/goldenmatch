"""Pure KG model + retrieval for the ER-KG demo.

No goldenmatch, no network, no erkgbench.adapters import -- unit-tested offline,
mirroring narrative.py. A partition is list[list[int]] over record indices; the
maps index->mention/type/context describe each record. run_demo.py adapts the
harness adapter output into these inputs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    node_id: int                      # stable: min record index in the cluster
    names: tuple[str, ...]            # distinct surface forms, sorted
    type: str
    context: str
    record_indices: tuple[int, ...]


@dataclass(frozen=True)
class KG:
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class Subgraph:
    query: str
    nodes: tuple[Node, ...]


def build_kg(
    partition: list[list[int]],
    mentions: dict[int, str],
    types: dict[int, str],
    contexts: dict[int, str],
) -> KG:
    """Turn a (complete) partition into entity nodes. type/context are shared
    within a real entity; taken deterministically from the min-index record."""
    nodes: list[Node] = []
    for cluster in partition:
        idxs = sorted(cluster)
        if not idxs:
            continue
        names = tuple(sorted({mentions[i] for i in idxs}))
        head = idxs[0]
        nodes.append(
            Node(
                node_id=head,
                names=names,
                type=types[head],
                context=contexts[head],
                record_indices=tuple(idxs),
            )
        )
    nodes.sort(key=lambda n: n.node_id)
    return KG(nodes=tuple(nodes))


def retrieve(kg: KG, query: str, *, type_filter: str | None = None, max_distractors: int = 2) -> Subgraph:
    """Deterministic retrieval: every node whose names contain `query`
    (case-insensitive exact match on a surface form), plus up to
    `max_distractors` other nodes of the same type (lowest node_id first)."""
    q = query.casefold()
    matched = [n for n in kg.nodes if any(name.casefold() == q for name in n.names)]
    matched_ids = {n.node_id for n in matched}
    base_type = type_filter or (matched[0].type if matched else None)
    distractors = [
        n for n in kg.nodes
        if n.node_id not in matched_ids and (base_type is None or n.type == base_type)
    ][:max_distractors]
    nodes = sorted([*matched, *distractors], key=lambda n: n.node_id)
    return Subgraph(query=query, nodes=tuple(nodes))
