"""Dimension hierarchy discovery via functional dependencies (PR-11).

A drill-down hierarchy (`country > state > city`) is a chain of functional
dependencies: the finer level determines the coarser (each city has exactly one
state). This module detects those FDs among a table's dimension columns and assembles
the maximal coarse->fine chains.

Deterministic (no LLM) and default-on. The FD test is a **near**-FD (a configurable
fraction of determinant groups map to a single dependent value, default 0.95) so a few
dirty rows don't kill a genuine hierarchy. Each column's *immediate* parent is the
highest-cardinality coarser column it determines, which yields clean chains rather than
transitive-closure noise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_DEFAULT_FD_THRESHOLD = 0.95


@dataclass(frozen=True)
class Hierarchy:
    """A discovered drill-down hierarchy. `levels` are ordered coarse -> fine (the
    drill path); `confidence` is the weakest FD edge in the chain."""

    table: str
    levels: list[str]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "levels": list(self.levels), "confidence": self.confidence}


def _distinct_count(table: Any, col: str) -> int:
    import pyarrow.compute as pc

    return len(pc.unique(table.column(col)))


def _fd_strength(table: Any, det: str, dep: str) -> float:
    """Fraction of `det` groups that map to a single `dep` value — the near-FD
    strength of ``det -> dep``. 1.0 = a perfect functional dependency."""
    import pyarrow as pa

    t = pa.table({det: table.column(det), dep: table.column(dep)})
    grouped = t.group_by(det).aggregate([(dep, "count_distinct")])
    counts = grouped.column(f"{dep}_count_distinct").to_pylist()
    if not counts:
        return 0.0
    return sum(1 for c in counts if c == 1) / len(counts)


def discover_hierarchies(
    table: Any,
    columns: list[str],
    *,
    table_name: str = "",
    threshold: float = _DEFAULT_FD_THRESHOLD,
) -> list[Hierarchy]:
    """Detect coarse->fine dimension hierarchies among `columns` of `table`.

    Only pyarrow Tables are read (the shape discovery runs on); a column not present
    is skipped. Returns one `Hierarchy` per maximal chain of length >= 2, sorted for
    determinism.
    """
    cols = [c for c in columns if c in table.column_names]
    if len(cols) < 2:
        return []

    card = {c: _distinct_count(table, c) for c in cols}

    # immediate parent(a) = the highest-cardinality STRICTLY-coarser column that `a`
    # near-determines (the closest coarser level, not a transitive grandparent).
    parent: dict[str, str] = {}
    conf: dict[str, float] = {}
    for a in cols:
        best: tuple[int, float, str] | None = None
        for b in cols:
            if b == a or card[b] >= card[a]:  # a parent must be strictly coarser
                continue
            s = _fd_strength(table, a, b)
            if s >= threshold and (best is None or card[b] > best[0]):
                best = (card[b], s, b)
        if best is not None:
            parent[a], conf[a] = best[2], best[1]

    parents = set(parent.values())
    leaves = [c for c in cols if c in parent and c not in parents]  # finest ends of a chain

    out: list[Hierarchy] = []
    for leaf in leaves:
        path = [leaf]  # fine -> coarse
        confs: list[float] = []
        node = leaf
        while node in parent:
            confs.append(conf[node])
            node = parent[node]
            path.append(node)
        if len(path) >= 2:
            out.append(Hierarchy(table=table_name, levels=list(reversed(path)),
                                 confidence=min(confs)))

    out.sort(key=lambda h: h.levels)
    return out
