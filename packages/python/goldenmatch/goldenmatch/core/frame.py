"""Backend-neutral Frame/Column seam for the Polars eviction (W0 scaffold).

Pipeline code will route through this instead of raw ``pl.DataFrame`` so call
sites can migrate off Polars wave by wave (spec:
docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md).
W0 shipped the delegating Polars backend; W1 adds the ``ArrowFrame`` backend
over ``pa.Table``, byte-value-equivalent to the Polars backend. ``to_frame``
is idempotent so a caller may pass a raw ``pl.DataFrame``, a raw ``pa.Table``,
or an already-wrapped ``Frame``.

Op-set discipline: SEMANTIC operations only, added as call sites port -- never
a Polars-expression clone. New ops require both backends plus a delegation-
parity test.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from goldenmatch._polars_lazy import pl


@runtime_checkable
class Column(Protocol):
    def __len__(self) -> int: ...
    def null_count(self) -> int: ...
    def n_unique(self) -> int: ...
    def to_list(self) -> list: ...
    def to_arrow(self) -> Any: ...


@runtime_checkable
class Frame(Protocol):
    @property
    def columns(self) -> list[str]: ...
    @property
    def height(self) -> int: ...
    @property
    def native(self) -> Any: ...
    def column(self, name: str) -> Column: ...
    def to_arrow_columns(self, names: list[str]) -> dict[str, Any]: ...


class PolarsColumn:
    """Delegates each op to the exact Polars call it replaces (byte-identical)."""

    __slots__ = ("_s",)

    def __init__(self, s: Any) -> None:
        self._s = s

    def __len__(self) -> int:
        return len(self._s)

    def null_count(self) -> int:
        return self._s.null_count()

    def n_unique(self) -> int:
        return self._s.n_unique()

    def to_list(self) -> list:
        return self._s.to_list()

    def to_arrow(self) -> Any:
        return self._s.to_arrow()


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

    def to_arrow_columns(self, names: list[str]) -> dict[str, Any]:
        # The fused-kernel FFI boundary: dict[str, pa.Array/ChunkedArray],
        # exactly the `collected_df[c].to_arrow()` shape pipeline.py builds today.
        return {n: self._df[n].to_arrow() for n in names}


class ArrowColumn:
    """Delegates each op to the pyarrow-compute call matching Polars semantics."""

    __slots__ = ("_col",)

    def __init__(self, col: Any) -> None:
        self._col = col

    def __len__(self) -> int:
        return len(self._col)

    def null_count(self) -> int:
        return self._col.null_count

    def n_unique(self) -> int:
        # mode="all" folds null into a single distinct group, matching Polars'
        # Series.n_unique() (which counts null as one distinct value).
        import pyarrow.compute as pc

        return pc.count_distinct(self._col, mode="all").as_py()

    def to_list(self) -> list:
        return self._col.to_pylist()

    def to_arrow(self) -> Any:
        return self._col


class ArrowFrame:
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

    def to_arrow_columns(self, names: list[str]) -> dict[str, Any]:
        return {n: self._tbl.column(n) for n in names}


def to_frame(obj: Any) -> Frame:
    """Idempotent coercion: raw ``pl.DataFrame``/``pa.Table`` or ``Frame`` -> ``Frame``."""
    if isinstance(obj, (PolarsFrame, ArrowFrame)):
        return obj
    if isinstance(obj, pl.DataFrame):
        return PolarsFrame(obj)

    import pyarrow as pa

    if isinstance(obj, pa.Table):
        return ArrowFrame(obj)
    raise TypeError(f"to_frame expects a polars DataFrame, pyarrow Table, or Frame, got {type(obj)!r}")
