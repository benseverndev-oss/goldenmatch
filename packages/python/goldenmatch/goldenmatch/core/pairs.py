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


# ---------------------------------------------------------------------------
# Arrow-native roadmap Phase 3 (#625): columnar dedup_pairs
# ---------------------------------------------------------------------------
#
# Sibling to ``dedup_pairs_max_score`` that accepts the Phase-1 pair-stream
# DataFrame (``PAIR_STREAM_SCHEMA``: id_a, id_b, score) and returns the same
# shape. Pure Polars expressions -- canonicalize via ``min_horizontal`` /
# ``max_horizontal``, then ``group_by(["id_a", "id_b"]).agg(pl.col("score").max())``,
# then sort.
#
# Why this is Phase 3 (not just Phase 1 polish): the existing dict-shaped
# native kernel benched at 1.19x speedup vs the Python loop -- the per-tuple
# pyo3 marshalling cost capped the win. The columnar path here bypasses that
# entirely (zero Python in the inner loop; only Polars expression evaluation
# in native code). At the 200M-pair / 5M-row reference shape the dict-shaped
# kernel paid ~80 bytes of Python overhead per pair; this path pays only the
# Arrow buffer cost (~24 bytes/pair). When the canonical pair stream is
# already a DataFrame (Phase 1c-real), this is the right kernel to call.
#
# Spec: docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md
# (gitignored).


def dedup_pairs_max_score_columnar(pairs_df):  # pl.DataFrame -> pl.DataFrame
    """Columnar dedup: canonicalize ``(id_a, id_b)`` then keep max ``score``
    per canonical pair.

    Args:
        pairs_df: Polars DataFrame with ``PAIR_STREAM_SCHEMA`` shape
            (``id_a: i64``, ``id_b: i64``, ``score: f64``).

    Returns:
        New DataFrame with the same schema, one row per canonical pair,
        sorted ascending by ``(id_a, id_b)``. Empty input returns an empty
        frame with the canonical schema.

    Contract:
        Equivalent to ``dedup_pairs_max_score(pairs_df_to_list(pairs_df))``
        then ``pairs_list_to_df(result)``, but vectorized via Polars
        expressions. On a score tie within a canonical pair, the
        returned score is the (numerically) maximum -- since the
        list-path kernel keeps the FIRST-occurrence tie via strict
        ``>`` but stores the same value, the OUTPUT scores are
        identical regardless of tie-break semantics.
    """
    import polars as _pl

    from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA

    if pairs_df.is_empty():
        return _pl.DataFrame(schema=PAIR_STREAM_SCHEMA)

    # Canonicalize via Polars min/max horizontal. Build new columns
    # ``__a__`` and ``__b__`` so the original ``id_a`` / ``id_b`` are
    # available to the group_by-then-rename step without column name
    # collisions during the intermediate computation.
    return (
        pairs_df
        .with_columns([
            _pl.min_horizontal("id_a", "id_b").alias("__a__"),
            _pl.max_horizontal("id_a", "id_b").alias("__b__"),
        ])
        .group_by(["__a__", "__b__"], maintain_order=False)
        .agg(_pl.col("score").max())
        .sort(["__a__", "__b__"])
        .rename({"__a__": "id_a", "__b__": "id_b"})
        .select(["id_a", "id_b", "score"])
    )


def dedup_pairs_max_score_arrow(pairs_df):  # pl.DataFrame -> pl.DataFrame
    """Rust-Arrow native dedup. Reads the DataFrame's Arrow buffers
    directly via the C Data Interface, runs the BTreeMap reduction in
    Rust, returns the result as a new DataFrame.

    Phase 3 deliverable per the Arrow-native roadmap (#625). The
    dict-shaped ``dedup_pairs_max_score`` Rust kernel benched at 1.19x
    (capped by per-tuple pyo3 marshalling); this Arrow path bypasses
    the marshalling floor entirely -- the i64/f64 arrays are read in
    place from the Polars frame's Arrow buffers, the BTreeMap reduces
    them, and the output emits back as Arrow arrays.

    Falls back to ``dedup_pairs_max_score_columnar`` (the Polars
    expression path) when the native ``pairs`` component is disabled
    or unavailable, so callers always get a working result.

    Bit-exact contract with the dict-shaped kernel: same canonical
    ``(min, max)`` orientation, same first-occurrence-wins tie
    semantics (output values identical regardless).
    """
    import polars as _pl

    from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA

    if not native_enabled("pairs"):
        return dedup_pairs_max_score_columnar(pairs_df)
    if pairs_df.is_empty():
        return _pl.DataFrame(schema=PAIR_STREAM_SCHEMA)

    native = native_module()
    if not hasattr(native, "dedup_pairs_arrow"):
        # Older native build doesn't have the Arrow kernel yet; degrade
        # to the Polars columnar path (same correctness, no perf claim).
        return dedup_pairs_max_score_columnar(pairs_df)

    a_arrow = pairs_df["id_a"].to_arrow()
    b_arrow = pairs_df["id_b"].to_arrow()
    s_arrow = pairs_df["score"].to_arrow()
    a_out, b_out, s_out = native.dedup_pairs_arrow(a_arrow, b_arrow, s_arrow)
    return _pl.DataFrame({
        "id_a": _pl.from_arrow(a_out),
        "id_b": _pl.from_arrow(b_out),
        "score": _pl.from_arrow(s_out),
    })
