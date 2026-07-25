"""Per-cell data-quality scoring -- the bridge GoldenMatch consumes for
quality-weighted survivorship.

``cell_quality(df)`` returns a SPARSE map ``{(row_index, column): weight}`` where
``weight`` is in ``(0, 1]`` and a *missing* entry means a clean cell (weight 1.0).
Only cells GoldenCheck can pinpoint as lower-quality are penalized, using signals
it already computes per-cell:

- **Fuzzy non-canonical values** (string columns): within a near-duplicate value
  cluster (`California`/`Californa`/`CALIFORNIA`), the most frequent spelling is
  canonical; cells holding a *variant* are penalized. So when GoldenMatch merges
  a cluster, the canonical spelling wins survivorship.
- **Future-dated values** (date/datetime columns): a timestamp after "now" is
  almost always wrong, so a real date beats a 2099 one when merging.

Null cells are NOT penalized here -- GoldenMatch's survivorship already ignores
nulls (it only chooses among non-null values).

``row_index`` is the 0-based positional index into ``df``; the caller maps it to
its own row id. Internal columns (``__``-prefixed) are skipped.

**Arrow-native.** GoldenMatch is arrow-native (polars was evicted), so this
bridge operates on a ``pyarrow.Table`` via ``pyarrow.compute`` -- no Polars in
the path. A Polars ``DataFrame`` (or any object exposing ``to_arrow()``) is
accepted for back-compat and coerced to Arrow once. The fuzzy near-duplicate
clustering runs on the native kernel (or its ``list[str]`` Python fallback) as
before -- that half was always frame-agnostic.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.profilers.fuzzy_values import _MAX_DISTINCT as _FUZZY_MAX_DISTINCT
from goldencheck.profilers.fuzzy_values import (
    _MIN_DISTINCT,
    _MIN_ROWS,
    _MIN_SIMILARITY,
    _python_clusters,
)

__all__ = ["cell_quality"]

# Penalty weights (a clean cell is 1.0). A cell hit by multiple signals keeps
# the lowest (worst) weight.
_PENALTY_FUZZY_VARIANT = 0.6
_PENALTY_FUTURE_DATED = 0.3


def _to_arrow(df: Any) -> pa.Table:
    """Coerce the input to a ``pyarrow.Table`` without importing Polars.

    Accepts an Arrow table directly (the GoldenMatch arrow-native path); a Polars
    ``DataFrame`` (or anything else exposing ``to_arrow()``) is converted via its
    own method, so this stays polars-free."""
    if isinstance(df, pa.Table):
        return df
    to_arrow = getattr(df, "to_arrow", None)
    if callable(to_arrow):
        out = to_arrow()
        if isinstance(out, pa.Table):
            return out
        return pa.table(out)
    return pa.table(df)


def _clusters(values: list[str]) -> list[list[int]]:
    if native_enabled("fuzzy_values"):
        try:
            return native_module().near_duplicate_value_clusters(values, _MIN_SIMILARITY)
        except Exception:  # noqa: BLE001 - native failure -> Python fallback
            return _python_clusters(values, _MIN_SIMILARITY)
    return _python_clusters(values, _MIN_SIMILARITY)


def _apply(scores: dict[tuple[int, str], float], idx: int, col: str, weight: float) -> None:
    key = (idx, col)
    prev = scores.get(key, 1.0)
    if weight < prev:
        scores[key] = weight


def _true_indices(mask: pa.ChunkedArray | pa.Array) -> list[int]:
    """0-based positional indices where a boolean array is True (nulls = False)."""
    return [i for i, v in enumerate(mask.to_pylist()) if v]


def _fuzzy_penalties(
    col_arr: pa.ChunkedArray, col: str, n_rows: int, scores: dict[tuple[int, str], float]
) -> None:
    distinct = pc.unique(col_arr.drop_null())
    n_distinct = len(distinct)
    if n_rows < _MIN_ROWS or n_distinct < _MIN_DISTINCT or n_distinct > _FUZZY_MAX_DISTINCT:
        return
    values: list[str] = distinct.to_pylist()
    clusters = _clusters(values)
    if not clusters:
        return

    # Frequency per value -> canonical = most frequent variant in each cluster.
    vc = pc.value_counts(col_arr)  # StructArray of {values, counts}
    freq = dict(zip(vc.field("values").to_pylist(), vc.field("counts").to_pylist()))

    penalized: set[str] = set()
    for cluster in clusters:
        members = [values[i] for i in cluster]
        canonical = max(members, key=lambda v: freq.get(v, 0))
        penalized.update(v for v in members if v != canonical)
    if not penalized:
        return

    mask = pc.fill_null(pc.is_in(col_arr, value_set=pa.array(list(penalized))), False)
    for idx in _true_indices(mask):
        _apply(scores, int(idx), col, _PENALTY_FUZZY_VARIANT)


def _future_penalties(
    col_arr: pa.ChunkedArray, col: str, scores: dict[tuple[int, str], float]
) -> None:
    t = col_arr.type
    if pa.types.is_date(t):
        now: Any = pa.scalar(_dt.date.today(), type=t)
    else:  # timestamp / datetime
        now = pa.scalar(_dt.datetime.now())
    try:
        mask = pc.fill_null(pc.greater(col_arr, now), False)
        future_idx = _true_indices(mask)
    except Exception:  # noqa: BLE001 - tz-aware vs naive, exotic dtype
        return
    for idx in future_idx:
        _apply(scores, int(idx), col, _PENALTY_FUTURE_DATED)


def cell_quality(df: Any) -> dict[tuple[int, str], float]:
    """Sparse per-cell quality weights for quality-weighted survivorship.

    Returns ``{(row_index, column): weight}`` for penalized cells only; a clean
    cell is absent (treat as 1.0). Accepts a ``pyarrow.Table`` (arrow-native
    path) or any object exposing ``to_arrow()`` (e.g. a Polars ``DataFrame``)."""
    tbl = _to_arrow(df)
    scores: dict[tuple[int, str], float] = {}
    if tbl.num_rows < 2:
        return scores
    n_rows = tbl.num_rows
    for col in tbl.column_names:
        if col.startswith("__"):  # internal columns (row id, source, ...)
            continue
        col_arr = tbl.column(col)
        t = col_arr.type
        if pa.types.is_string(t) or pa.types.is_large_string(t):
            _fuzzy_penalties(col_arr, col, n_rows, scores)
        elif pa.types.is_date(t) or pa.types.is_timestamp(t):
            _future_penalties(col_arr, col, scores)
    return scores
