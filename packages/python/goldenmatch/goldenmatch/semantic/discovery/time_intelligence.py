"""Time intelligence (PR-13).

Detect a table's primary time dimension (with a data-inferred grain + drill
granularities) and derive per-measure time variants (MTD / YoY / rolling), so the
semantic layer can compute time comparisons automatically. Deterministic, default-on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_GRAIN_ORDER = ["day", "week", "month", "quarter", "year"]
# Name hints for preferring one date column as THE primary time dimension.
_TIME_NAME_HINTS = ("date", "time", "created", "timestamp", "period", "day")


@dataclass(frozen=True)
class TimeDimension:
    """The primary time dimension: its `grain` (finest, inferred from the data) and the
    drill `granularities` up from it."""

    table: str
    column: str
    grain: str
    granularities: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "column": self.column, "grain": self.grain,
                "granularities": list(self.granularities)}


@dataclass(frozen=True)
class TimeMetric:
    """A time-windowed metric variant over a base measure (MTD / YoY / rolling)."""

    name: str
    base: str
    kind: str  # mtd | yoy | rolling
    window: str | None = None
    grain_to_date: str | None = None
    table: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "base": self.base, "kind": self.kind,
                "window": self.window, "grain_to_date": self.grain_to_date, "table": self.table}


def _infer_grain(table: Any, col: str) -> str:
    """Finest granularity the data actually resolves: time-of-day -> day; all values on
    month/quarter/year starts -> that coarser grain."""
    import pyarrow.compute as pc

    arr = table.column(col)
    try:
        days = pc.day(arr).drop_null().to_pylist()
        months = pc.month(arr).drop_null().to_pylist()
    except Exception:  # noqa: BLE001 - not a temporal column
        return "day"
    if not days:
        return "day"
    try:  # a timestamp with any time-of-day resolves to (at least) day
        if any(h != 0 for h in pc.hour(arr).drop_null().to_pylist()):
            return "day"
    except Exception:  # noqa: BLE001 - date32 has no hour
        pass
    if all(d == 1 for d in days):
        if all(m == 1 for m in months):
            return "year"
        if all(m in (1, 4, 7, 10) for m in months):
            return "quarter"
        return "month"
    return "day"


def _granularities_from(grain: str) -> list[str]:
    return _GRAIN_ORDER[_GRAIN_ORDER.index(grain):] if grain in _GRAIN_ORDER else [grain]


def _pick_time_column(dimensions: list[Any]) -> str | None:
    date_cols = [d.column for d in dimensions if getattr(d, "kind", "") == "date"]
    if not date_cols:
        return None
    for c in date_cols:  # prefer a name-hinted column
        if any(h in c.lower() for h in _TIME_NAME_HINTS):
            return c
    return date_cols[0]


def discover_time_dimension(
    table: Any, dimensions: list[Any], *, table_name: str = "",
) -> TimeDimension | None:
    """The primary time dimension for a table, or None when it has no date column."""
    col = _pick_time_column(dimensions)
    if col is None:
        return None
    grain = _infer_grain(table, col)
    return TimeDimension(table=table_name, column=col, grain=grain,
                         granularities=_granularities_from(grain))


def discover_time_metrics(
    measures: list[Any], time_dimension: TimeDimension | None, *, table_name: str = "",
) -> list[TimeMetric]:
    """Per sum-safe measure, the MTD / YoY / rolling-7d variants (empty without a time
    dimension)."""
    if time_dimension is None:
        return []
    tn = table_name or time_dimension.table
    out: list[TimeMetric] = []
    for m in measures:
        if not getattr(m, "safe_to_sum", False):
            continue
        col = m.column
        out.append(TimeMetric(f"{col}_mtd", col, "mtd", grain_to_date="month", table=tn))
        out.append(TimeMetric(f"{col}_yoy", col, "yoy", window="1 year", table=tn))
        out.append(TimeMetric(f"{col}_rolling_7d", col, "rolling", window="7 days", table=tn))
    return out
