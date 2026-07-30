"""Shared deterministic entity-level split helper for the multi-source ER data
pipeline (Task 2). CPU/box-safe: stdlib only, never imports torch/transformers.

`gen_pairs.py` delegates its local `_split_of` to this module so the split
logic has one source of truth across the synthetic generator and any
benchmark/real-dataset `PairSource` that needs entity-level train/val/test
partitioning."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable


def entity_keys_from_edges(
    record_ids: Iterable[str], edges: Iterable[tuple[str, str]]
) -> dict[str, str]:
    """Map each record id to a stable entity key via connected components over the
    gold match edges. Records with no edges are their own singleton entity. The key
    is the min member id, so it is deterministic regardless of union order."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path halving
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rid in record_ids:
        find(rid)
    for a, b in edges:
        union(a, b)

    groups: dict[str, list[str]] = {}
    for rid in list(parent):
        groups.setdefault(find(rid), []).append(rid)
    key_of: dict[str, str] = {}
    for members in groups.values():
        canonical = min(members)
        for m in members:
            key_of[m] = canonical
    return key_of


def split_of(eid: int | str, *, seed: int, val_frac: float, test_frac: float,
             holdout_domain: str | None = None, domain: str | None = None) -> str:
    """Deterministic entity-level split. Holdout domain -> 'test'; else hash
    (seed, str(eid)) into train/val/test. str(eid) so benchmark string IDs work."""
    if holdout_domain and domain == holdout_domain:
        return "test"
    h = hashlib.sha256(f"{seed}:{eid}".encode()).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    if frac < test_frac:
        return "test"
    if frac < test_frac + val_frac:
        return "val"
    return "train"
