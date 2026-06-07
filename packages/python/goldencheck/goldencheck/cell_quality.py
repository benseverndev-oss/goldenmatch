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
its own row id. Internal columns (``__``-prefixed) are skipped. Pure-Polars +
the optional native fuzzy kernel; degrades to the Python fallback when the
``goldencheck`` native extension isn't installed.
"""
from __future__ import annotations

import datetime as _dt

import polars as pl

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


def _fuzzy_penalties(df: pl.DataFrame, col: str, scores: dict[tuple[int, str], float]) -> None:
    s = df[col]
    distinct = s.drop_nulls().unique()
    n_distinct = distinct.len()
    if df.height < _MIN_ROWS or n_distinct < _MIN_DISTINCT or n_distinct > _FUZZY_MAX_DISTINCT:
        return
    values: list[str] = distinct.to_list()
    clusters = _clusters(values)
    if not clusters:
        return

    # Frequency per value -> canonical = most frequent variant in each cluster.
    vc = s.value_counts()
    count_col = vc.columns[-1]  # "count" (name has varied across polars versions)
    freq = dict(zip(vc[col].to_list(), vc[count_col].to_list()))

    penalized: set[str] = set()
    for cluster in clusters:
        members = [values[i] for i in cluster]
        canonical = max(members, key=lambda v: freq.get(v, 0))
        penalized.update(v for v in members if v != canonical)
    if not penalized:
        return

    for idx in s.is_in(list(penalized)).fill_null(False).arg_true().to_list():
        _apply(scores, int(idx), col, _PENALTY_FUZZY_VARIANT)


def _future_penalties(df: pl.DataFrame, col: str, scores: dict[tuple[int, str], float]) -> None:
    s = df[col]
    is_date = s.dtype == pl.Date
    now: _dt.date | _dt.datetime = _dt.date.today() if is_date else _dt.datetime.now()
    try:
        mask = (s > now).fill_null(False)
        future_idx = mask.arg_true().to_list()
    except Exception:  # noqa: BLE001 - tz-aware vs naive, exotic dtype
        return
    for idx in future_idx:
        _apply(scores, int(idx), col, _PENALTY_FUTURE_DATED)


def cell_quality(df: pl.DataFrame) -> dict[tuple[int, str], float]:
    """Sparse per-cell quality weights for quality-weighted survivorship.

    Returns ``{(row_index, column): weight}`` for penalized cells only; a clean
    cell is absent (treat as 1.0)."""
    scores: dict[tuple[int, str], float] = {}
    if df.height < 2:
        return scores
    for col in df.columns:
        if col.startswith("__"):  # internal columns (row id, source, ...)
            continue
        dtype = df[col].dtype
        if dtype == pl.Utf8:
            _fuzzy_penalties(df, col, scores)
        elif dtype in (pl.Date, pl.Datetime):
            _future_penalties(df, col, scores)
    return scores
