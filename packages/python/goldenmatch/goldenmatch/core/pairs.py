"""Native Core pair + candidate-estimation primitives.

Pure-Python reference implementations are the source of truth; each delegates to
the optional ``goldenmatch._native`` kernel (Rust) when the ``pairs`` component
is enabled (see ``_native_loader``). The native and Python paths are bit-exact —
integer arithmetic plus a strict-``>`` max reduction, no float tolerance — so
``pairs`` is gated on by default once parity is verified.

These are the low-level primitives the native-runtime roadmap calls the "Native
Core": pair canonicalization, max-score dedup, and candidate estimation. The
public surface is re-exported from ``goldenmatch.native``.

All pairs follow the project-wide canonical form ``(min(a, b), max(a, b))``.
"""
from __future__ import annotations

import math

from goldenmatch.core._native_loader import native_enabled, native_module

Pair = tuple[int, int, float]


def canonicalize_pairs(pairs: list[Pair]) -> list[Pair]:
    """Canonicalize each pair to ``(min, max, score)``.

    Input order and duplicates are preserved; only endpoint orientation is
    normalized.
    """
    if native_enabled("pairs"):
        return native_module().canonicalize_pairs(pairs)
    return [(a, b, s) if a <= b else (b, a, s) for a, b, s in pairs]


def dedup_pairs_max_score(pairs: list[Pair]) -> list[Pair]:
    """Canonicalize, then keep the maximum score per canonical pair.

    Output is sorted ascending by ``(a, b)``. On a score tie the first
    occurrence wins (strict ``>`` guard), matching the native kernel.
    """
    if native_enabled("pairs"):
        return native_module().dedup_pairs_max_score(pairs)
    best: dict[tuple[int, int], float] = {}
    for a, b, s in pairs:
        key = (a, b) if a <= b else (b, a)
        if key not in best or s > best[key]:
            best[key] = s
    return [(a, b, best[(a, b)]) for (a, b) in sorted(best)]


def candidate_pair_count(block_sizes: list[int]) -> int:
    """Total candidate comparisons across blocks: ``sum(n*(n-1)//2)``."""
    if native_enabled("pairs"):
        return native_module().candidate_pair_count(block_sizes)
    return sum(n * (n - 1) // 2 for n in block_sizes if n >= 2)


def block_histogram(block_sizes: list[int]) -> dict[str, int]:
    """Block-size distribution summary.

    Returns ``count``, ``total_records``, ``max``, and nearest-rank ``p50`` /
    ``p95`` / ``p99`` (the percentile definition in ``core/cluster.py``). Empty
    input yields all zeros.
    """
    if native_enabled("pairs"):
        count, total, mx, p50, p95, p99 = native_module().block_histogram(block_sizes)
        return {
            "count": count,
            "total_records": total,
            "max": mx,
            "p50": p50,
            "p95": p95,
            "p99": p99,
        }
    sizes = sorted(block_sizes)
    count = len(sizes)
    if count == 0:
        return {"count": 0, "total_records": 0, "max": 0, "p50": 0, "p95": 0, "p99": 0}

    def pct(q: float) -> int:
        idx = max(0, min(count - 1, int(math.ceil(q * count)) - 1))
        return sizes[idx]

    return {
        "count": count,
        "total_records": sum(sizes),
        "max": sizes[-1],
        "p50": pct(0.5),
        "p95": pct(0.95),
        "p99": pct(0.99),
    }


def connected_components(
    pairs: list[Pair], all_ids: list[int] | None = None
) -> list[list[int]]:
    """Connected components over ``all_ids`` plus all pair endpoints.

    Public wrapper over the native Union-Find kernel (``core/cluster.py`` uses
    the same kernel internally for ``build_clusters``). Falls back to the
    pure-Python ``UnionFind``. Component and member order are unspecified.
    """
    if all_ids is None:
        seen: set[int] = set()
        for a, b, _s in pairs:
            seen.add(a)
            seen.add(b)
        all_ids = list(seen)
    if native_enabled("pairs"):
        return native_module().connected_components(list(pairs), all_ids)
    from goldenmatch.core.cluster import UnionFind

    uf = UnionFind()
    uf.add_many(all_ids)
    for a, b, _s in pairs:
        uf.union(a, b)
    return [sorted(c) for c in uf.get_clusters()]
