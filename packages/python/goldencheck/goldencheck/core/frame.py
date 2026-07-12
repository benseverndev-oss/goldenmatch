"""Backend-neutral Frame/Column seam for the Polars eviction (P0).

Profilers route through this instead of a raw ``pl.DataFrame`` so their bodies can
migrate off Polars one at a time. P0 ships only the Polars-backed backend; the
native/Arrow backend arrives in a later stage. ``to_frame`` is idempotent so a
caller may pass either a raw ``pl.DataFrame`` or an already-wrapped ``Frame``.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from goldencheck._polars_lazy import pl
from goldencheck.core._native_loader import native_enabled, native_module


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
    def str_len_chars(self) -> Column: ...
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


def dtype_category(pl_dtype: Any) -> str:
    """Public single entry point for the neutral dtype vocabulary
    (str/int/uint/float/date/datetime/bool/other). Mirrors the Rust
    kernel's ``dtype_category`` string-for-string. Callers that need the
    neutral category for a dtype gate should route through this rather
    than reimplementing tuple/equality checks against ``pl.*`` dtypes.
    """
    return _neutral_dtype(pl_dtype)


_CAST_KIND = {"float": "Float64", "int": "Int64", "str": "String"}   # strings only; resolved via getattr in cast()

# Standard numeric-literal shapes for the vectorized ArrowColumn string->numeric
# cast (RE2, anchored full-match). Match what float()/int() accept for the common
# case; the owned contract treats non-standard tokens (inf/nan/underscored) as
# non-numeric -> null, which is the sensible answer for "is this column numeric".
_INT_LITERAL_RE = r"^[+-]?\d+$"
_FLOAT_LITERAL_RE = r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$"


class NativeRequiredError(RuntimeError):
    """A covered hard op needs the native regex kernel. Install it with
    `pip install goldencheck[native]` (or build it in-tree)."""


def _VC_KEY(kv: tuple[Any, int]) -> tuple[int, bool, Any]:
    value, count = kv
    return (-count, value is None, value if value is not None else "")   # count DESC, nulls-last, value ASC


def _regex_kernel():
    if not native_enabled("regex"):
        raise NativeRequiredError(
            "goldencheck native regex kernel unavailable; the encoding/format/"
            "pattern_consistency checks need `pip install goldencheck[native]`."
        )
    return native_module()


def _date_kernel():
    if not native_enabled("str_to_date"):
        raise NativeRequiredError(
            "goldencheck native date kernel unavailable; the temporal check needs "
            "`pip install goldencheck[native]`."
        )
    return native_module()


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
        return dtype_category(self._s.dtype)

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

    def str_len_chars(self) -> PolarsColumn:
        return PolarsColumn(self._s.str.len_chars())

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        vc = self._s.value_counts()
        pairs = zip(vc[self._s.name].to_list(), vc["count"].to_list())   # (value, count)
        return sorted(pairs, key=_VC_KEY)

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
    """Pure-Python column wrapping a ``list`` — the mechanical, dtype-free ops used
    by the simple column profilers (nullability/cardinality/uniqueness), plus the
    native-regex-backed string ops (str_match_count/str_filter/str_replace_all) and
    eq/filter_by used by the hard profilers (encoding/format/pattern_consistency).
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
        if isinstance(first, datetime):
            return "datetime"
        if isinstance(first, date):
            return "date"
        if isinstance(first, int):
            return "int"
        if isinstance(first, float):
            return "float"
        if isinstance(first, str):
            return "str"
        return "other"

    def str_match_count(self, pattern: str) -> int:
        return _regex_kernel().str_contains_count(self._v, pattern)

    def str_filter(self, pattern: str, *, matching: bool) -> PyColumn:
        mask = _regex_kernel().str_filter_mask(self._v, pattern)   # list[bool | None]
        return PyColumn([v for v, m in zip(self._v, mask) if m is not None and m == matching])

    def str_replace_all(self, pattern: str, value: str) -> PyColumn:
        return PyColumn(_regex_kernel().str_replace_all(self._v, pattern, value))

    def str_len_chars(self) -> PyColumn:
        return PyColumn([None if v is None else len(v) for v in self._v])

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        return sorted(Counter(self._v).items(), key=_VC_KEY)

    def eq(self, value: Any) -> PyColumn:
        return PyColumn([v == value if v is not None else None for v in self._v])

    def filter_by(self, mask: PyColumn) -> PyColumn:
        return PyColumn([v for v, m in zip(self._v, mask._v) if m])

    def str_to_date(self, fmt: str, *, strict: bool) -> PyColumn:
        if strict:
            raise NotImplementedError("goldencheck str_to_date supports strict=False only")
        iso = _date_kernel().str_to_date(self._v, fmt)
        return PyColumn([date.fromisoformat(s) if s is not None else None for s in iso])

    def gt_mask(self, other: PyColumn) -> PyColumn:
        return PyColumn([None if a is None or b is None else a > b
                          for a, b in zip(self._v, other._v)])

    def fill_null(self, value: Any) -> PyColumn:
        return PyColumn([value if v is None else v for v in self._v])

    def sum(self) -> Any:
        return sum(v for v in self._v if v is not None)

    def cast(self, kind: str, *, strict: bool = False) -> PyColumn:
        if kind != "str":
            raise NotImplementedError(f"PyColumn.cast supports 'str' only, got {kind!r}")
        return PyColumn([None if v is None else str(v) for v in self._v])


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


def _arrow():
    """Lazy pyarrow accessor — mirrors ``_polars_lazy`` so ``import
    goldencheck.core.frame`` works without pyarrow for the pure-Polars path.
    Returns ``(pyarrow, pyarrow.compute)``."""
    import pyarrow as pa
    import pyarrow.compute as pc

    return pa, pc


def _arrow_neutral_dtype(arr_type: Any) -> str:
    """Arrow type -> neutral dtype vocabulary (str/int/uint/float/date/datetime/
    bool/other). MUST agree with ``dtype_category(pl_dtype)`` for the same data so
    ``ArrowColumn.dtype == PolarsColumn.dtype``."""
    import pyarrow.types as pat

    if pat.is_string(arr_type) or pat.is_large_string(arr_type):
        return "str"
    if pat.is_unsigned_integer(arr_type):   # must precede is_integer (true for both)
        return "uint"
    if pat.is_integer(arr_type):
        return "int"
    if pat.is_floating(arr_type):
        return "float"
    if pat.is_date(arr_type):
        return "date"
    if pat.is_timestamp(arr_type):
        return "datetime"
    if pat.is_boolean(arr_type):
        return "bool"
    return "other"


class ArrowColumn:
    """``pyarrow.Array``-backed column implementing the full ``Column`` Protocol.

    Parity oracle is ``PolarsColumn``: for the same data every method returns a
    byte/epsilon-identical result, EXCEPT ``dtype_repr`` (see its docstring). The
    numeric reductions route through the native ``column_numeric_stats`` kernel
    (one cached call); temporal/string/bool reductions use ``pyarrow.compute`` so
    ``max()`` on a date column returns a ``datetime.date`` (freshness needs it).
    """

    __slots__ = ("_arr", "_cat", "_num_stats")

    def __init__(self, arr: Any) -> None:
        pa, _ = _arrow()
        if isinstance(arr, pa.ChunkedArray):
            arr = arr.combine_chunks()
        self._arr = arr
        self._cat = _arrow_neutral_dtype(arr.type)
        self._num_stats: Any = None   # None=unset, False=empty/all-null, tuple=cached

    # -- numeric stats (cached single kernel call) ----------------------------
    def _numeric_stats(self):
        """Cached ``(count_nonnull, min, max, mean, std, sum)`` for numeric
        columns, or ``None`` when empty/all-null (guarded before the kernel).

        Native ``column_numeric_stats`` is the fast path; when the kernel is
        absent/disabled (``native_enabled("numeric_stats")`` False) a
        ``pyarrow.compute`` fallback computes the identical tuple within the
        already-accepted stat epsilon (``std`` uses ddof=1 to match
        Polars/kernel). Restores the accelerator-not-requirement contract."""
        if self._num_stats is not None:
            return None if self._num_stats is False else self._num_stats
        arr = self._arr
        if len(arr) == 0 or arr.null_count == len(arr):
            self._num_stats = False
            return None
        if native_enabled("numeric_stats"):
            self._num_stats = native_module().column_numeric_stats(arr)
        else:
            self._num_stats = self._numeric_stats_pyarrow(arr)
        return self._num_stats

    @staticmethod
    def _numeric_stats_pyarrow(arr):
        """``pyarrow.compute`` mirror of ``column_numeric_stats``: returns
        ``(count_nonnull, min, max, mean, std, sum)``. ``std`` is ddof=1 (Polars
        parity) and is ``None`` when <2 non-null values (as the native kernel's
        std-guard is applied downstream in ``std()``)."""
        _, pc = _arrow()
        count_nonnull = len(arr) - arr.null_count
        mm = pc.min_max(arr, skip_nulls=True)
        vmin = mm["min"].as_py()
        vmax = mm["max"].as_py()
        mean = pc.mean(arr, skip_nulls=True).as_py()
        total = pc.sum(arr, skip_nulls=True).as_py()
        if count_nonnull >= 2:
            std_scalar = pc.stddev(arr, ddof=1, skip_nulls=True)
            std = std_scalar.as_py() if std_scalar.is_valid else None
        else:
            std = None
        return (count_nonnull, vmin, vmax, mean, std, total)

    def _is_numeric(self) -> bool:
        return self._cat in ("int", "uint", "float")

    def _scalar(self, value: Any) -> Any:
        """Wrap a comparison value as an Arrow scalar of THIS column's type so
        date/datetime comparisons work; numeric values pass straight through."""
        if self._cat in ("date", "datetime"):
            pa, _ = _arrow()
            return pa.scalar(value, type=self._arr.type)
        return value

    # -- Column Protocol ------------------------------------------------------
    def __len__(self) -> int:
        return len(self._arr)

    def null_count(self) -> int:
        return self._arr.null_count

    def n_unique(self) -> int:
        _, pc = _arrow()
        return int(pc.count_distinct(self._arr, mode="all").as_py())   # nulls = 1 distinct (Polars parity)

    def drop_nulls(self) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.drop_null(self._arr))

    def unique(self) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.unique(self._arr))

    def sort(self) -> ArrowColumn:
        _, pc = _arrow()
        idx = pc.sort_indices(self._arr, null_placement="at_start")   # ascending, nulls-first (Polars)
        return ArrowColumn(self._arr.take(idx))

    def to_list(self) -> list:
        return self._arr.to_pylist()

    @property
    def dtype(self) -> str:
        return self._cat

    def dtype_repr(self) -> str:
        # OWNED-contract dtype vocabulary: returns the NEUTRAL category, unlike
        # PolarsColumn.dtype_repr which returns raw ``str(pl.dtype)``. This
        # divergence is EXPECTED and measured by the Flip differential, not a bug.
        return self._cat

    def to_arrow(self) -> Any:
        return self._arr

    def get(self, index: int) -> Any:
        return self._arr[index].as_py()

    def _cast_str_numeric(self, target: Any, pattern: str) -> ArrowColumn:
        """Vectorized string -> numeric cast with null-on-unparseable (polars
        strict=False). Regex-mask the values that are NOT a standard numeric
        literal to null, then a single vectorized pc.cast -- no per-element
        Python loop (that loop made type_inference's cast the dominant scan cost:
        2M float()/append calls on a 1M-row column). Leading/trailing whitespace
        is trimmed first to match Python float()/int() acceptance.
        """
        pa, pc = _arrow()
        a = self._arr
        stripped = pc.utf8_trim_whitespace(a)
        looks_numeric = pc.match_substring_regex(stripped, pattern)
        cleaned = pc.if_else(looks_numeric, stripped, pa.scalar(None, type=a.type))
        return ArrowColumn(pc.cast(cleaned, target))

    def cast(self, kind: str, *, strict: bool = False) -> ArrowColumn:
        pa, pc = _arrow()
        import pyarrow.types as pat

        a = self._arr
        is_str = pat.is_string(a.type) or pat.is_large_string(a.type)
        if kind == "float":
            if is_str:
                return self._cast_str_numeric(pa.float64(), _FLOAT_LITERAL_RE)
            return ArrowColumn(pc.cast(a, pa.float64(), safe=False))
        if kind == "int":
            if is_str:
                return self._cast_str_numeric(pa.int64(), _INT_LITERAL_RE)
            return ArrowColumn(pc.cast(a, pa.int64(), safe=False))
        if kind == "str":
            return ArrowColumn(pc.cast(a, pa.string()))
        raise KeyError(kind)

    def member_count(self, values: list) -> int:
        pa, pc = _arrow()
        r = pc.sum(pc.is_in(self._arr, value_set=pa.array(values))).as_py()
        return int(r or 0)

    def str_match_count(self, pattern: str) -> int:
        if native_enabled("regex"):
            return _regex_kernel().str_contains_count(self._arr.to_pylist(), pattern)
        _, pc = _arrow()
        mask = pc.match_substring_regex(self._arr, pattern)   # null preserved on null input
        return int(pc.sum(mask, skip_nulls=True).as_py() or 0)

    def str_filter(self, pattern: str, *, matching: bool) -> ArrowColumn:
        pa, pc = _arrow()
        if native_enabled("regex"):
            vals = self._arr.to_pylist()
            mask = _regex_kernel().str_filter_mask(vals, pattern)   # list[bool | None]
            kept = [v for v, m in zip(vals, mask) if m is not None and m == matching]
            return ArrowColumn(pa.array(kept, type=pa.string()))
        # pyarrow fallback: match_substring_regex -> bool mask (null on null input);
        # ~mask is null-preserving so nulls drop from BOTH matching/non-matching
        # (Polars' filter drops null-mask rows), matching PyColumn's `m is not None`.
        mask = pc.match_substring_regex(self._arr, pattern)
        sel = mask if matching else pc.invert(mask)
        return ArrowColumn(pc.filter(self._arr, sel, null_selection_behavior="drop"))

    def min(self) -> Any:
        if self._is_numeric():
            s = self._numeric_stats()
            if s is None:
                return None
            return int(round(s[1])) if self._cat in ("int", "uint") else s[1]
        _, pc = _arrow()
        return pc.min(self._arr).as_py()

    def max(self) -> Any:
        if self._is_numeric():
            s = self._numeric_stats()
            if s is None:
                return None
            return int(round(s[2])) if self._cat in ("int", "uint") else s[2]
        _, pc = _arrow()
        return pc.max(self._arr).as_py()

    def mean(self) -> Any:
        if not self._is_numeric():
            return None
        s = self._numeric_stats()
        return None if s is None else s[3]

    def std(self) -> Any:
        if not self._is_numeric():
            return None
        s = self._numeric_stats()
        if s is None or s[0] < 2:   # ddof=1 needs >=2 non-null (Polars -> None)
            return None
        return s[4]

    def diff(self) -> ArrowColumn:
        pa, pc = _arrow()
        import pyarrow.types as pat

        a = self._arr
        # pl.Series.diff() dtype rule: SIGNED ints keep their type and WRAP
        # (Int8 diff stays Int8, wrapping_sub); UNSIGNED ints upcast to the next
        # wider SIGNED type (UInt8->Int16, UInt32->Int64) so diffs can go negative.
        if pat.is_unsigned_integer(a.type):
            widen = {1: pa.int16(), 2: pa.int32(), 4: pa.int64(), 8: pa.int64()}
            a = pc.cast(a, widen[a.type.bit_width // 8], safe=False)
        n = len(a)
        null_head = pa.array([None], type=a.type)
        if n == 0:
            return ArrowColumn(a.slice(0, 0))
        if n == 1:
            return ArrowColumn(null_head)
        # pc.subtract WRAPS on overflow (non-checked variant) -> matches Int64
        # diff's wrapping_sub; narrower ints were widened above so never overflow.
        d = pc.subtract(a.slice(1), a.slice(0, n - 1))
        return ArrowColumn(pa.concat_arrays([null_head, d]))

    def is_sorted(self) -> bool:
        _, pc = _arrow()
        a = self._arr
        if len(a) <= 1:
            return True
        idx = pc.sort_indices(a, null_placement="at_start")   # ascending nulls-first (Polars)
        return a.equals(a.take(idx))

    def count_gt(self, value: Any) -> int:
        _, pc = _arrow()
        r = pc.sum(pc.greater(self._arr, self._scalar(value))).as_py()
        return int(r or 0)

    def count_eq(self, value: Any) -> int:
        _, pc = _arrow()
        r = pc.sum(pc.equal(self._arr, self._scalar(value))).as_py()
        return int(r or 0)

    def filter_outside(self, lower: Any, upper: Any) -> ArrowColumn:
        _, pc = _arrow()
        a = self._arr
        mask = pc.or_(pc.less(a, lower), pc.greater(a, upper))
        return ArrowColumn(pc.filter(a, mask))

    def slice(self, offset: int, length: int | None = None) -> ArrowColumn:
        if length is None:
            return ArrowColumn(self._arr.slice(offset))
        return ArrowColumn(self._arr.slice(offset, length))

    def str_replace_all(self, pattern: str, value: str) -> ArrowColumn:
        pa, pc = _arrow()
        if native_enabled("regex"):
            out = _regex_kernel().str_replace_all(self._arr.to_pylist(), pattern, value)
            return ArrowColumn(pa.array(out, type=pa.string()))
        # pyarrow fallback: replace_substring_regex preserves nulls, matching
        # Polars' str.replace_all. Cast to string() so the result column dtype
        # matches the native path (input may be large_string).
        out = pc.replace_substring_regex(self._arr, pattern, value)
        return ArrowColumn(pc.cast(out, pa.string()))

    def str_len_chars(self) -> ArrowColumn:
        # pc.utf8_length -> per-value character count (nulls preserved), matching
        # pl.Series.str.len_chars(). The result is an integer column so
        # ``.mean()`` routes through the numeric-stats kernel like Polars' mean.
        _, pc = _arrow()
        return ArrowColumn(pc.utf8_length(self._arr))

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        return sorted(Counter(self._arr.to_pylist()).items(), key=_VC_KEY)

    def eq(self, value: Any) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.equal(self._arr, self._scalar(value)))

    def filter_by(self, mask: Column) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.filter(self._arr, mask._arr, null_selection_behavior="drop"))

    def is_null(self) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.is_null(self._arr))

    def gt_mask(self, other: Column) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.greater(self._arr, other._arr))

    def eq_mask(self, other: Column) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.equal(self._arr, other._arr))

    def fill_null(self, value: Any) -> ArrowColumn:
        _, pc = _arrow()
        return ArrowColumn(pc.fill_null(self._arr, value))

    def sum(self) -> Any:
        if self._cat == "bool":
            # Polars Series.sum() on Boolean returns the count of True (nulls
            # skipped) as an int; pc.sum over a bool array does the same.
            _, pc = _arrow()
            r = pc.sum(self._arr).as_py()
            return int(r or 0)
        if not self._is_numeric():
            return None
        s = self._numeric_stats()
        if s is None:
            return 0   # Polars empty/all-null numeric sum -> 0
        return int(round(s[5])) if self._cat in ("int", "uint") else s[5]

    def str_to_date(self, fmt: str, *, strict: bool) -> ArrowColumn:
        pa, pc = _arrow()
        if strict:
            raise NotImplementedError("goldencheck str_to_date supports strict=False only")
        if native_enabled("str_to_date"):
            iso = _date_kernel().str_to_date(self._arr.to_pylist(), fmt)
            vals = [date.fromisoformat(s) if s is not None else None for s in iso]
            return ArrowColumn(pa.array(vals, type=pa.date32()))
        # pyarrow fallback: strptime with error_is_null (== Polars strict=False:
        # unparseable -> null) then narrow the timestamp to date32.
        ts = pc.strptime(self._arr, format=fmt, unit="s", error_is_null=True)
        return ArrowColumn(pc.cast(ts, pa.date32()))


class ArrowFrame:
    """``pyarrow.Table``-backed frame implementing the ``Frame`` Protocol."""

    __slots__ = ("_tbl",)

    def __init__(self, tbl: Any) -> None:
        self._tbl = tbl

    @property
    def columns(self) -> list[str]:
        return self._tbl.column_names

    @property
    def height(self) -> int:
        return self._tbl.num_rows

    @property
    def native(self) -> Any:
        return self._tbl

    def column(self, name: str) -> ArrowColumn:
        return ArrowColumn(self._tbl.column(name))


def to_frame(native: Any) -> Frame:
    if isinstance(native, (PolarsFrame, PyFrame, ArrowFrame)):
        return native
    if isinstance(native, pl.DataFrame):
        return PolarsFrame(native)
    try:
        import pyarrow as pa
    except ImportError:
        pa = None
    if pa is not None and isinstance(native, pa.Table):
        return ArrowFrame(native)
    raise TypeError(
        f"to_frame() expects a polars.DataFrame, pyarrow.Table, PolarsFrame, PyFrame, "
        f"or ArrowFrame; got {type(native)!r}"
    )
