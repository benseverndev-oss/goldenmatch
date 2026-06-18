"""Adapter contract + shared clustering helpers.

An adapter wraps one system's entity-dedup behaviour. Given the records (every
adapter sees the same ``mention`` strings; multi-field adapters may also read
``entity_type`` / ``context``), it returns a clustering: a list of clusters,
each a list of record indices. Singletons may be omitted -- only pairs matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: The cost a deterministic / string-only adapter reports: it spent nothing on
#: any paid API. Most adapters in the suite are deterministic, so this is the
#: default reported by ``last_cost_of`` for any adapter that doesn't override.
ZERO_COST: dict = {"llm_calls": 0, "llm_tokens": 0, "llm_usd": 0.0}


@dataclass(frozen=True)
class Record:
    index: int
    mention: str
    entity_type: str
    context: str


@runtime_checkable
class Adapter(Protocol):
    #: Short identifier shown in the results table.
    name: str
    #: One-line description of the documented default rule (with source).
    defaults: str
    #: True if the modelled rule is deterministic. Real LLM-judge layers
    #: (Graphiti / mem0) are non-deterministic by construction -- see the note
    #: each adapter carries.
    deterministic: bool
    #: Fidelity tier of this row: "real" (the actual system, e.g. goldenmatch),
    #: "real-inproc"/"real-live" (real framework run), "validated" (modeled but
    #: confirmed vs source), or "modeled" (modeled, unverified).
    fidelity: str

    def resolve(self, records: list[Record]) -> list[list[int]]:
        ...


class AdapterBase:
    """Default behaviour shared by every adapter.

    The single source of the zero-cost default: a deterministic / string-only
    adapter makes no paid API calls, so its ``last_cost()`` is all zeros. Only
    the adapters that actually spend money (goldenmatch's LLM + paid-embedding
    paths) override ``last_cost``; everyone else inherits this.
    """

    def last_cost(self) -> dict:
        """Cost of the most recent ``resolve()`` call.

        ``{'llm_calls': int, 'llm_tokens': int, 'llm_usd': float}``. Zeros for
        every deterministic adapter.
        """
        return dict(ZERO_COST)


def last_cost_of(adapter: object) -> dict:
    """Cost of ``adapter``'s most recent ``resolve()`` call, defaulting to zeros.

    ``last_cost()`` is an OPTIONAL part of the adapter contract: an adapter that
    never touches a paid API need not implement it. This accessor lets the
    runner record cost uniformly -- it returns the adapter's ``last_cost()`` if
    it has one, else :data:`ZERO_COST` -- so adapters that don't subclass
    :class:`AdapterBase` still report a sane zero cost without each having to
    spell the method out.
    """
    fn = getattr(adapter, "last_cost", None)
    if callable(fn):
        return fn()
    return dict(ZERO_COST)


# ── clustering primitives ────────────────────────────────────────────────


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_by_key(records: list[Record], keyfn) -> list[list[int]]:
    """Bucket records by an exact key (the exact-match family)."""
    buckets: dict[object, list[int]] = {}
    for r in records:
        buckets.setdefault(keyfn(r), []).append(r.index)
    return list(buckets.values())


def cluster_by_pairwise(records: list[Record], predicate) -> list[list[int]]:
    """Transitive closure of an all-pairs ``predicate(a, b) -> bool``.

    O(n^2) -- faithful to the all-pairs resolvers (neo4j-graphrag-python) and
    fine at benchmark scale. ``predicate`` is only called for a<b.
    """
    n = len(records)
    uf = _UnionFind(n)
    pos = {r.index: r for r in records}
    idxs = [r.index for r in records]
    for ai in range(n):
        for bi in range(ai + 1, n):
            a, b = pos[idxs[ai]], pos[idxs[bi]]
            if predicate(a, b):
                uf.union(a.index, b.index)
    groups: dict[int, list[int]] = {}
    for i in idxs:
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())
