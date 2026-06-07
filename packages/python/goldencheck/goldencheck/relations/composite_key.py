"""Composite-key discovery (cross-column relation profiler).

GoldenCheck already reports *single*-column candidate keys (a column that is
100% unique + non-null) via baseline constraints and the identity-safe-PK
preflight. This profiler covers the case those miss: tables whose natural key is
a *combination* of columns -- e.g. ``(order_id, line_no)`` or
``(date, store, sku)`` -- where no single column is unique on its own.

It searches for **minimal** column subsets (size 2..MAX_KEY_SIZE) whose tuples
are all distinct, skipping supersets of a smaller key found. The combinatorial
distinct-tuple counting is the expensive part; when ``goldencheck[native]`` is
installed it runs in the Rust kernel (``composite_key_search``), otherwise it
falls back to the identical Polars-driven search here. Both paths return the
same minimal-key set -- asserted in tests/core/test_native_parity.py.

Reported only when NO single-column key exists (that's the case where the
composite key is the actual story); emitted as INFO -- it's positive structural
information, not a violation.
"""
from __future__ import annotations

import polars as pl

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.models.finding import Finding, Severity

# A key wider than this is rarely a meaningful natural key and the search space
# grows fast; cap it. Candidate columns are likewise capped (most-discriminative
# first) so a very wide table can't blow up the search.
MAX_KEY_SIZE = 3
MAX_CANDIDATE_COLS = 12
MAX_REPORTED_KEYS = 3

# Dtypes the native interner supports; we restrict the Python path to the same
# set so the two are parity-comparable on identical inputs.
_SUPPORTED = (
    pl.Utf8,
    pl.Int8, pl.Int16, pl.Int32, pl.Int64,
    pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    pl.Float32, pl.Float64,
    pl.Boolean,
)


def _select_candidates(df: pl.DataFrame, n_rows: int) -> list[str]:
    """Non-constant, supported-dtype columns, most-discriminative first, capped."""
    scored: list[tuple[int, str]] = []
    for col in df.columns:
        series = df[col]
        if series.dtype not in _SUPPORTED:
            continue
        nu = series.n_unique()
        if nu <= 1:  # constant column can never help form a key
            continue
        scored.append((nu, col))
    # Highest cardinality first -- the columns most likely to complete a key.
    scored.sort(key=lambda t: t[0], reverse=True)
    return [col for _nu, col in scored[:MAX_CANDIDATE_COLS]]


def _has_single_column_key(df: pl.DataFrame, n_rows: int) -> bool:
    for col in df.columns:
        series = df[col]
        if series.null_count() == 0 and series.n_unique() == n_rows:
            return True
    return False


def _python_search(
    df: pl.DataFrame, candidates: list[str], n_rows: int, max_size: int
) -> list[list[int]]:
    """Pure-Python mirror of goldencheck_core::composite_key_search.

    Identical BFS: candidates are all non-unique here (we only run when no
    single-column key exists), subsets stay sorted via the ``c <= last`` guard,
    and supersets of a found key are pruned."""
    idxs = list(range(len(candidates)))
    found: list[list[int]] = []
    cap = min(max_size, len(idxs))
    frontier: list[list[int]] = [[i] for i in idxs]
    for _size in range(2, cap + 1):
        nxt: list[list[int]] = []
        for base in frontier:
            last = base[-1]
            for c in idxs:
                if c <= last:
                    continue
                subset = base + [c]
                if any(all(x in subset for x in k) for k in found):
                    continue
                cols = [candidates[j] for j in subset]
                if df.select(cols).n_unique() == n_rows:
                    found.append(subset)
                else:
                    nxt.append(subset)
        if not nxt:
            break
        frontier = nxt
    return found


class CompositeKeyProfiler:
    """Dataset-level relation profiler: discover minimal composite keys."""

    def profile(self, df: pl.DataFrame) -> list[Finding]:
        n_rows = df.height
        if n_rows < 2 or df.width < 2:
            return []
        # Only interesting when there's no single-column key.
        if _has_single_column_key(df, n_rows):
            return []

        candidates = _select_candidates(df, n_rows)
        if len(candidates) < 2:
            return []

        # candidates are all non-unique here, so single_unique is all-False;
        # passed through for kernel-signature parity.
        single_unique = [False] * len(candidates)

        keys_idx: list[list[int]]
        if native_enabled("composite_keys"):
            try:
                arrays = [df[c].to_arrow() for c in candidates]
                keys_idx = native_module().composite_key_search(
                    arrays, MAX_KEY_SIZE, single_unique
                )
            except Exception:  # noqa: BLE001 - any native failure -> Python path
                keys_idx = _python_search(df, candidates, n_rows, MAX_KEY_SIZE)
        else:
            keys_idx = _python_search(df, candidates, n_rows, MAX_KEY_SIZE)

        if not keys_idx:
            return []

        # Smallest keys first, then deterministic; report a handful.
        keys = [[candidates[i] for i in idxs] for idxs in keys_idx]
        keys.sort(key=lambda k: (len(k), k))
        findings: list[Finding] = []
        for key in keys[:MAX_REPORTED_KEYS]:
            cols_str = ", ".join(key)
            findings.append(Finding(
                severity=Severity.INFO,
                # Anchor on the first key column (avoids flagging every column
                # in benchmarks); the full set is in the message + metadata.
                column=key[0],
                check="composite_key",
                message=(
                    f"Columns ({cols_str}) form a composite key — together they "
                    f"uniquely identify every row, and no single column does."
                ),
                affected_rows=n_rows,
                sample_values=[],
                suggestion=(
                    "Use this column set as the natural join/dedup key, or add a "
                    "stable single-column surrogate key (UUID / autoincrement)."
                ),
                confidence=0.6,
                metadata={"technique": "composite_key", "key_columns": key},
            ))
        return findings
