"""Backend-agnostic columnar container — the seam for evicting Polars as a hard
dependency (see docs/design/2026-07-07-polars-eviction-plan.md).

The engine operates on a ``Frame`` instead of touching ``pl.DataFrame`` directly.
Phase 0 ships only the Polars backend (``PolarsFrame``), so behavior is byte-
identical; the point is that the container-level operations (columns / height /
column get+set / head / rename / drop / dedup / filter) now go through an interface
a native/Arrow backend can implement without Polars.

The per-transform DISPATCH (evaluating a Polars ``Expr``, a ``Series`` transform, or
a ``dataframe``-mode function) still uses the backend's native column type via the
``.native`` escape hatch — abstracting the transform signature itself is a later
phase. Everything a backend MUST provide to run the engine is on this interface;
``.native`` is the explicit, greppable boundary of what remains Polars-coupled.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from goldenflow._polars_lazy import pl


@runtime_checkable
class Frame(Protocol):
    """The columnar-container contract the engine depends on. Implemented by
    ``PolarsFrame`` today; a native/Arrow (and pure-Python) backend implements the
    same surface to make Polars optional."""

    @property
    def native(self) -> Any:
        """The underlying backend object (``pl.DataFrame`` for ``PolarsFrame``).
        The transform-dispatch code still reaches through this; every use is a
        remaining Polars coupling to port in a later phase."""

    @property
    def columns(self) -> list[str]: ...

    @property
    def height(self) -> int: ...

    def dtype(self, name: str) -> Any: ...

    def column(self, name: str) -> Any:
        """The named column in the backend's native form (``pl.Series`` today)."""

    def with_column(self, name: str, col: Any) -> Frame:
        """Return a new frame with ``name`` replaced by ``col`` (backend column)."""

    def replace_native(self, native: Any) -> Frame:
        """Wrap a fresh backend object (e.g. the result of a dataframe-mode
        transform) in the same Frame type."""

    def head(self, n: int) -> Frame: ...

    def rename(self, mapping: dict[str, str]) -> Frame: ...

    def drop(self, cols: list[str]) -> Frame: ...

    def unique(self, subset: list[str], keep: str) -> Frame: ...

    def filter_not_null(self, column: str) -> Frame: ...

    def filter_cmp(self, column: str, op: str, value: str) -> Frame:
        """Row filter ``column <op> value`` where ``op`` is ``">"`` or ``"<"``."""


class PolarsFrame:
    """Polars-backed :class:`Frame` — the current behavior, byte-identical."""

    __slots__ = ("_df",)

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    @property
    def native(self) -> pl.DataFrame:
        return self._df

    @property
    def columns(self) -> list[str]:
        return self._df.columns

    @property
    def height(self) -> int:
        return self._df.height

    def dtype(self, name: str) -> Any:
        return self._df.schema.get(name)

    def column(self, name: str) -> pl.Series:
        return self._df[name]

    def with_column(self, name: str, col: pl.Series) -> PolarsFrame:
        return PolarsFrame(self._df.with_columns(col.alias(name)))

    def replace_native(self, native: pl.DataFrame) -> PolarsFrame:
        return PolarsFrame(native)

    def head(self, n: int) -> PolarsFrame:
        return PolarsFrame(self._df.head(n))

    def rename(self, mapping: dict[str, str]) -> PolarsFrame:
        return PolarsFrame(self._df.rename(mapping))

    def drop(self, cols: list[str]) -> PolarsFrame:
        return PolarsFrame(self._df.drop(cols))

    def unique(self, subset: list[str], keep: str) -> PolarsFrame:
        return PolarsFrame(self._df.unique(subset=subset, keep=keep))  # type: ignore[arg-type]

    def filter_not_null(self, column: str) -> PolarsFrame:
        return PolarsFrame(self._df.filter(pl.col(column).is_not_null()))

    def filter_cmp(self, column: str, op: str, value: str) -> PolarsFrame:
        expr = pl.col(column) > value if op == ">" else pl.col(column) < value
        return PolarsFrame(self._df.filter(expr))


class ColumnarFrame:
    """Polars-free :class:`Frame` over ``dict[str, list]`` -- the shape the
    columnar engine already carries.

    Exists so the frame-level ops (renames / drop / filters / dedup) stop being
    a reason to fall back to the polars engine. They were never expressive
    operations; they were unimplemented ones, and "install polars" was standing
    in for a few dozen lines of list manipulation.

    Two deliberate differences from ``PolarsFrame``, both stated rather than
    hidden:

    * ``unique`` PRESERVES INPUT ORDER. ``pl.DataFrame.unique`` does not
      guarantee order unless ``maintain_order=True``, so the polars path's row
      order here is unspecified rather than specified-and-different. Preserving
      it is a narrowing of unspecified behaviour, not a contradiction of it.
    * ``dtype`` reports the Python type of the first non-null value, since a
      dict of lists carries no schema. The columnar engine treats CSV columns as
      TEXT by design (it will not coerce "01234" to 1234), so this answers
      ``str`` where polars might have inferred a numeric dtype.
    """

    __slots__ = ("_cols",)

    def __init__(self, cols: dict[str, list]) -> None:
        self._cols = cols

    @property
    def native(self) -> dict[str, list]:
        return self._cols

    @property
    def columns(self) -> list[str]:
        return list(self._cols)

    @property
    def height(self) -> int:
        return len(next(iter(self._cols.values()))) if self._cols else 0

    def dtype(self, name: str) -> Any:
        for v in self._cols.get(name, []):
            if v is not None:
                return type(v)
        return type(None)

    def column(self, name: str) -> list:
        return self._cols[name]

    def with_column(self, name: str, col: list) -> ColumnarFrame:
        out = dict(self._cols)
        out[name] = list(col)
        return ColumnarFrame(out)

    def replace_native(self, native: dict[str, list]) -> ColumnarFrame:
        return ColumnarFrame(native)

    def head(self, n: int) -> ColumnarFrame:
        return ColumnarFrame({k: v[:n] for k, v in self._cols.items()})

    def rename(self, mapping: dict[str, str]) -> ColumnarFrame:
        return ColumnarFrame({mapping.get(k, k): v for k, v in self._cols.items()})

    def drop(self, cols: list[str]) -> ColumnarFrame:
        drop_set = set(cols)
        return ColumnarFrame({k: v for k, v in self._cols.items() if k not in drop_set})

    def _take(self, idx: list[int]) -> ColumnarFrame:
        return ColumnarFrame({k: [v[i] for i in idx] for k, v in self._cols.items()})

    def unique(self, subset: list[str], keep: str) -> ColumnarFrame:
        seen: dict[tuple, int] = {}
        for i in range(self.height):
            key = tuple(self._cols[c][i] for c in subset)
            if keep == "last" or key not in seen:
                seen[key] = i
        return self._take(sorted(seen.values()))

    def filter_not_null(self, column: str) -> ColumnarFrame:
        col = self._cols[column]
        return self._take([i for i in range(self.height) if col[i] is not None])

    def filter_cmp(self, column: str, op: str, value: str) -> ColumnarFrame:
        col = self._cols[column]
        keep = []
        for i in range(self.height):
            v = col[i]
            if v is None:
                # A null comparison is null in polars, and `filter` drops null
                # mask rows -- so nulls are excluded on both paths.
                continue
            try:
                if (v > value) if op == ">" else (v < value):
                    keep.append(i)
            except TypeError:
                # Mismatched types compare as False rather than exploding; the
                # polars path raises here, and raising mid-filter on one bad row
                # is not better behaviour to reproduce.
                continue
        return self._take(keep)


def to_frame(df: Any) -> Frame:
    """Wrap a backend object in a :class:`Frame`. Today only ``pl.DataFrame`` (the
    public ``transform_df`` still takes/returns a ``pl.DataFrame``); the native
    backend registers here later."""
    if isinstance(df, (PolarsFrame, ColumnarFrame)):
        return df
    if isinstance(df, dict):
        return ColumnarFrame(df)
    if isinstance(df, pl.DataFrame):
        return PolarsFrame(df)
    raise TypeError(f"unsupported frame backend: {type(df).__name__}")
