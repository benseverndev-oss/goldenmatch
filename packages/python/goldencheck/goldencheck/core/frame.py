"""Backend-neutral Frame/Column seam for the Polars eviction (P0).

Profilers route through this instead of a raw ``pl.DataFrame`` so their bodies can
migrate off Polars one at a time. P0 ships only the Polars-backed backend; the
native/Arrow backend arrives in a later stage. ``to_frame`` is idempotent so a
caller may pass either a raw ``pl.DataFrame`` or an already-wrapped ``Frame``.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from goldencheck._polars_lazy import pl


@runtime_checkable
class Column(Protocol):
    def __len__(self) -> int: ...
    def null_count(self) -> int: ...
    def n_unique(self) -> int: ...
    def drop_nulls(self) -> Column: ...
    def unique(self) -> Column: ...
    def sort(self) -> Column: ...
    def to_list(self) -> list: ...
    @property
    def dtype(self) -> str: ...
    def dtype_repr(self) -> str: ...
    def to_arrow(self) -> Any: ...
    def get(self, index: int) -> Any: ...
    def cast(self, kind: str, *, strict: bool = False) -> Column: ...
    def member_count(self, values: list) -> int: ...
    def str_match_count(self, pattern: str) -> int: ...
    def str_filter(self, pattern: str, *, matching: bool) -> Column: ...
    def min(self) -> Any: ...
    def max(self) -> Any: ...
    def mean(self) -> Any: ...
    def std(self) -> Any: ...
    def diff(self) -> Column: ...
    def is_sorted(self) -> bool: ...
    def count_gt(self, value: Any) -> int: ...
    def count_eq(self, value: Any) -> int: ...
    def filter_outside(self, lower: Any, upper: Any) -> Column: ...
    def slice(self, offset: int, length: int | None = None) -> Column: ...
    def str_replace_all(self, pattern: str, value: str) -> Column: ...
    def value_counts_desc(self) -> list[tuple[Any, int]]: ...
    def eq(self, value: Any) -> Column: ...
    def filter_by(self, mask: Column) -> Column: ...
    def is_null(self) -> Column: ...
    def gt_mask(self, other: Column) -> Column: ...
    def eq_mask(self, other: Column) -> Column: ...
    def fill_null(self, value: Any) -> Column: ...
    def sum(self) -> Any: ...
    def str_to_date(self, fmt: str, *, strict: bool) -> Column: ...


@runtime_checkable
class Frame(Protocol):
    @property
    def columns(self) -> list[str]: ...
    @property
    def height(self) -> int: ...
    @property
    def native(self) -> Any: ...
    def column(self, name: str) -> Column: ...


def _neutral_dtype(dt: Any) -> str:
    if dt in (pl.Utf8, pl.String):
        return "str"
    if dt in (pl.Int8, pl.Int16, pl.Int32, pl.Int64):
        return "int"
    if dt in (pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
        return "uint"
    if dt in (pl.Float32, pl.Float64):
        return "float"
    if dt == pl.Date:
        return "date"
    if dt == pl.Datetime:
        return "datetime"
    if dt == pl.Boolean:
        return "bool"
    return "other"


_CAST_KIND = {"float": "Float64", "int": "Int64", "str": "String"}   # strings only; resolved via getattr in cast()


class PolarsColumn:
    __slots__ = ("_s",)

    def __init__(self, s: Any) -> None:
        self._s = s

    def __len__(self) -> int:
        return len(self._s)

    def null_count(self) -> int:
        return self._s.null_count()

    def n_unique(self) -> int:
        return self._s.n_unique()

    def drop_nulls(self) -> PolarsColumn:
        return PolarsColumn(self._s.drop_nulls())

    def unique(self) -> PolarsColumn:
        return PolarsColumn(self._s.unique())

    def sort(self) -> PolarsColumn:
        return PolarsColumn(self._s.sort())

    def to_list(self) -> list:
        return self._s.to_list()

    @property
    def dtype(self) -> str:
        return _neutral_dtype(self._s.dtype)

    def dtype_repr(self) -> str:
        return str(self._s.dtype)

    def to_arrow(self) -> Any:
        return self._s.to_arrow()

    def get(self, index: int) -> Any:
        return self._s[index]

    def cast(self, kind: str, *, strict: bool = False) -> PolarsColumn:
        pl_type = getattr(pl, _CAST_KIND[kind])
        return PolarsColumn(self._s.cast(pl_type, strict=strict))

    def member_count(self, values: list) -> int:
        return int(self._s.is_in(values).sum())

    def str_match_count(self, pattern: str) -> int:
        return int(self._s.str.contains(pattern).sum())

    def str_filter(self, pattern: str, *, matching: bool) -> PolarsColumn:
        mask = self._s.str.contains(pattern)
        return PolarsColumn(self._s.filter(mask if matching else ~mask))

    def min(self) -> Any:
        return self._s.min()

    def max(self) -> Any:
        return self._s.max()

    def mean(self) -> Any:
        return self._s.mean()

    def std(self) -> Any:
        return self._s.std()

    def diff(self) -> PolarsColumn:
        return PolarsColumn(self._s.diff())

    def is_sorted(self) -> bool:
        return bool(self._s.is_sorted())

    def count_gt(self, value: Any) -> int:
        return int((self._s > value).sum())

    def count_eq(self, value: Any) -> int:
        return int((self._s == value).sum())

    def filter_outside(self, lower: Any, upper: Any) -> PolarsColumn:
        return PolarsColumn(self._s.filter((self._s < lower) | (self._s > upper)))

    def slice(self, offset: int, length: int | None = None) -> PolarsColumn:
        return PolarsColumn(self._s.slice(offset, length))

    def str_replace_all(self, pattern: str, value: str) -> PolarsColumn:
        return PolarsColumn(self._s.str.replace_all(pattern, value))

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        vc = self._s.value_counts().sort("count", descending=True)
        return list(zip(vc[self._s.name].to_list(), vc["count"].to_list()))

    def eq(self, value: Any) -> PolarsColumn:
        return PolarsColumn(self._s == value)

    def filter_by(self, mask: Column) -> PolarsColumn:
        return PolarsColumn(self._s.filter(mask._s))

    def is_null(self) -> PolarsColumn:
        return PolarsColumn(self._s.is_null())

    def gt_mask(self, other: Column) -> PolarsColumn:
        return PolarsColumn(self._s > other._s)

    def eq_mask(self, other: Column) -> PolarsColumn:
        return PolarsColumn(self._s == other._s)

    def fill_null(self, value: Any) -> PolarsColumn:
        return PolarsColumn(self._s.fill_null(value))

    def sum(self) -> Any:
        return self._s.sum()

    def str_to_date(self, fmt: str, *, strict: bool) -> PolarsColumn:
        return PolarsColumn(self._s.str.to_date(format=fmt, strict=strict))


class PolarsFrame:
    __slots__ = ("_df",)

    def __init__(self, df: Any) -> None:
        self._df = df

    @property
    def columns(self) -> list[str]:
        return self._df.columns

    @property
    def height(self) -> int:
        return self._df.height

    @property
    def native(self) -> Any:
        return self._df

    def column(self, name: str) -> PolarsColumn:
        return PolarsColumn(self._df[name])


class PyColumn:
    """Pure-Python column wrapping a ``list`` — the 7 mechanical, dtype-free ops
    used by the simple column profilers (nullability/cardinality/uniqueness).
    Deliberately does NOT implement the full ``Column`` Protocol.
    """

    __slots__ = ("_v",)

    def __init__(self, values: list) -> None:
        self._v = values

    def __len__(self) -> int:
        return len(self._v)

    def null_count(self) -> int:
        return sum(1 for v in self._v if v is None)

    def n_unique(self) -> int:
        return len(set(self._v))

    def drop_nulls(self) -> PyColumn:
        return PyColumn([v for v in self._v if v is not None])

    def unique(self) -> PyColumn:
        return PyColumn(list(set(self._v)))

    def sort(self) -> PyColumn:
        return PyColumn(sorted(self._v))

    def to_list(self) -> list:
        return list(self._v)

    @property
    def dtype(self) -> str:
        non_null = [v for v in self._v if v is not None]
        if not non_null:
            return "other"                      # Polars infers pl.Null -> _neutral_dtype -> "other"
        first = non_null[0]
        if isinstance(first, bool):
            return "bool"
        if isinstance(first, int):
            return "int"
        if isinstance(first, float):
            return "float"
        if isinstance(first, str):
            return "str"
        return "other"


class PyFrame:
    """Pure-Python frame wrapping a ``dict[str, list]`` — no Polars import."""

    __slots__ = ("_cols",)

    def __init__(self, cols: dict[str, list]) -> None:
        self._cols = cols

    @classmethod
    def from_columns(cls, cols: dict[str, list]) -> PyFrame:
        return cls(cols)

    @property
    def columns(self) -> list[str]:
        return list(self._cols.keys())

    @property
    def height(self) -> int:
        return len(next(iter(self._cols.values()))) if self._cols else 0

    @property
    def native(self) -> Any:
        return self._cols

    def column(self, name: str) -> PyColumn:
        return PyColumn(self._cols[name])


def to_frame(native: Any) -> Frame:
    if isinstance(native, (PolarsFrame, PyFrame)):
        return native
    if isinstance(native, pl.DataFrame):
        return PolarsFrame(native)
    raise TypeError(
        f"to_frame() expects a polars.DataFrame, PolarsFrame, or PyFrame; got {type(native)!r}"
    )
