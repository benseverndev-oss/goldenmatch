"""List-shaped programmatic entry points to the deep-profiling kernels.

The profiler classes in ``baseline/``, ``relations/`` and ``profilers/`` reach
the CPU-bound kernels through DataFrame-shaped methods that emit
:class:`~goldencheck.models.finding.Finding` objects. This module exposes the
*same* native-gated kernels as thin functions over **plain Python lists**,
returning plain ints / index structures. That shape is what a columnar SQL UDF
needs: the caller aggregates a column into a ``LIST`` and gets index/count
structures back.

These are the single source of truth shared by:
  * the in-process programmatic API (import and call directly),
  * the DuckDB UDF surface (``goldenmatch-duckdb`` ``goldencheck_*`` functions),
  * and — value-for-value — the Postgres pgrx surface, which runs the identical
    ``goldencheck-core`` kernel in Rust (native-direct, no CPython).

Each function runs the native ``goldencheck-core`` kernel when it is enabled
(``GOLDENCHECK_NATIVE``) and importable, else the package's own pure-Python
fallback — the very functions the profilers use — so the two paths are
byte-identical (asserted in ``tests/core/test_kernels.py``).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from goldencheck.core._native_loader import native_enabled, native_module

__all__ = [
    "benford_histogram",
    "near_duplicate_clusters",
    "discover_functional_dependencies",
    "discover_approximate_fds",
    "composite_key_search",
    "denial_constraint_evidence",
]


# ── benford ──────────────────────────────────────────────────────────────────


def benford_histogram(values: Sequence[float]) -> list[int]:
    """Leading-digit (1..9) histogram over the numeric ``values``.

    Returns a 9-element list: index ``d-1`` is the count of values whose first
    significant digit is ``d``. Non-positive / non-finite values are skipped
    (matching the Benford conformance check). This is the kernel behind the
    ``benford_conformance`` statistical baseline check.
    """
    nums = [float(v) for v in values if v is not None]
    if native_enabled("benford"):
        try:
            import pyarrow as pa

            hist = native_module().benford_leading_digits(
                pa.array(nums, type=pa.float64())
            )
            return [int(x) for x in hist]
        except Exception:  # noqa: BLE001 - any native failure -> pure-Python path
            pass
    counts = [0] * 9
    for v in nums:
        if v <= 0 or not math.isfinite(v):
            continue
        exp = math.floor(math.log10(v))
        d = int(v / (10.0 ** exp))
        if 1 <= d <= 9:
            counts[d - 1] += 1
    return counts


# ── fuzzy near-duplicate values ──────────────────────────────────────────────


def near_duplicate_clusters(
    values: Sequence[str], min_similarity: float
) -> list[list[int]]:
    """Cluster near-duplicate string ``values`` (e.g. ``California`` /
    ``Californa`` / ``CALIFORNIA``).

    Returns clusters as lists of **row indices** into ``values``; singletons are
    omitted. Uses trigram+prefix blocking + Levenshtein ratio, identical to the
    ``fuzzy_duplicate_values`` profiler.
    """
    vals = [str(v) for v in values]
    if native_enabled("fuzzy_values"):
        try:
            return [list(c) for c in native_module().near_duplicate_value_clusters(
                vals, float(min_similarity)
            )]
        except Exception:  # noqa: BLE001 - any native failure -> pure-Python path
            pass
    from goldencheck.profilers.fuzzy_values import _python_clusters

    return _python_clusters(vals, float(min_similarity))


# ── functional dependencies (strict) ─────────────────────────────────────────


def discover_functional_dependencies(
    columns: Sequence[Sequence],
) -> list[tuple[int, int]]:
    """Discover strict single-column FDs among equal-length ``columns``.

    ``det -> dep`` holds iff every value of ``columns[det]`` maps to exactly one
    value of ``columns[dep]``. Returns ``(det_index, dep_index)`` pairs into the
    passed column list. Trivial pairs (unique determinant, constant dependent)
    are skipped, matching the ``functional_dependency`` profiler.
    """
    cols = [list(c) for c in columns]
    if len(cols) < 2:
        return []
    if native_enabled("functional_dependencies"):
        try:
            import pyarrow as pa

            arrays = [pa.array(c) for c in cols]
            return [
                (int(i), int(j))
                for i, j in native_module().discover_functional_dependencies(arrays)
            ]
        except Exception:  # noqa: BLE001 - any native failure -> Polars path
            pass
    from goldencheck._polars_lazy import pl
    from goldencheck.relations.functional_dependency import _discover_python

    names = [f"c{i}" for i in range(len(cols))]
    df = pl.DataFrame({n: c for n, c in zip(names, cols)})
    return _discover_python(df, names, df.height)


# ── approximate functional dependencies ──────────────────────────────────────


def discover_approximate_fds(
    columns: Sequence[Sequence], min_confidence: float
) -> list[tuple[int, int, int]]:
    """Discover *near*-strict FDs among ``columns`` and count their violations.

    Returns ``(det_index, dep_index, n_violation_rows)`` triples where
    ``det -> dep`` holds for at least ``min_confidence`` of rows (but not all).
    Same first-seen interning + mode tie-break + average-group guard as the
    ``fd_violation`` profiler.
    """
    cols = [list(c) for c in columns]
    if len(cols) < 2:
        return []
    if native_enabled("approximate_fd"):
        try:
            import pyarrow as pa

            arrays = [pa.array(c) for c in cols]
            return [
                (int(i), int(j), int(v))
                for i, j, v in native_module().discover_approximate_fds(
                    arrays, float(min_confidence)
                )
            ]
        except Exception:  # noqa: BLE001 - any native failure -> pure-Python path
            pass
    from goldencheck.relations.approx_fd import _discover_python, _intern

    n_rows = len(cols[0]) if cols else 0
    cols_ids = [_intern(c) for c in cols]
    return _discover_python(cols_ids, n_rows, float(min_confidence))


# ── composite keys ───────────────────────────────────────────────────────────


def composite_key_search(
    columns: Sequence[Sequence], max_size: int = 3
) -> list[list[int]]:
    """Find minimal composite keys (column subsets of size 2..``max_size`` whose
    tuples are all distinct) among ``columns``.

    Constant columns and columns that are a key on their own are excluded first
    (a single-column key needs no composite), then the minimal-subset search
    runs. Returns each key as a sorted list of **original** column indices.
    Mirrors the ``composite_key`` profiler's search.
    """
    cols = [list(c) for c in columns]
    if len(cols) < 2:
        return []
    n_rows = len(cols[0])
    if n_rows < 2:
        return []

    # Drop constant (can't help a key) and single-unique (is the key alone)
    # columns, exactly the candidate contract the kernel + fallback share.
    cand_orig: list[int] = []
    for i, c in enumerate(cols):
        nu = len(set(c))
        if 1 < nu < n_rows:
            cand_orig.append(i)
    if len(cand_orig) < 2:
        return []
    cand_cols = [cols[i] for i in cand_orig]
    size = int(max_size)

    keys_local: list[list[int]]
    if native_enabled("composite_keys"):
        try:
            import pyarrow as pa

            arrays = [pa.array(c) for c in cand_cols]
            single_unique = [False] * len(cand_cols)  # pre-filtered above
            keys_local = [
                list(k)
                for k in native_module().composite_key_search(
                    arrays, size, single_unique
                )
            ]
        except Exception:  # noqa: BLE001 - any native failure -> Python path
            keys_local = _composite_fallback(cand_cols, n_rows, size)
    else:
        keys_local = _composite_fallback(cand_cols, n_rows, size)

    # Map candidate-local indices back to original column positions.
    return [sorted(cand_orig[i] for i in key) for key in keys_local]


# ── denial-constraint evidence ───────────────────────────────────────────────


def denial_constraint_evidence(cols, nulls, pred_spec, which_pass, n, sample_idx):
    """Evidence map ``{u64_mask: count}`` for denial-constraint discovery. RICHER
    than the column-only kernels here: also takes the predicate spec.

    See ``denial/evidence.py`` for the bit layout, the ``(kind, col_a, op,
    col_b, literal)`` predicate encoding, and the pass semantics (``which_pass``
    1 = row / Pass 1 over ``n`` rows, 2 = pair / Pass 2 over ``sample_idx``).
    """
    if native_enabled("denial_constraint"):
        try:
            masks, counts = native_module().denial_constraint_evidence(
                cols, nulls, pred_spec, which_pass, n, sample_idx)
            return dict(zip(masks, counts))
        except Exception:  # noqa: BLE001 - any native failure -> pure-Python path
            pass
    from goldencheck.denial.evidence import _evidence_python

    return _evidence_python(cols, nulls, pred_spec, which_pass, n, sample_idx)


def _composite_fallback(
    cand_cols: list[list], n_rows: int, max_size: int
) -> list[list[int]]:
    from goldencheck._polars_lazy import pl
    from goldencheck.relations.composite_key import _python_search

    names = [f"c{i}" for i in range(len(cand_cols))]
    df = pl.DataFrame({n: c for n, c in zip(names, cand_cols)})
    return _python_search(df, names, n_rows, max_size)
