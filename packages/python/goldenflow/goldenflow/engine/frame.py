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


def to_frame(df: Any) -> Frame:
    """Wrap a backend object in a :class:`Frame`. Today only ``pl.DataFrame`` (the
    public ``transform_df`` still takes/returns a ``pl.DataFrame``); the native
    backend registers here later."""
    if isinstance(df, PolarsFrame):
        return df
    if isinstance(df, pl.DataFrame):
        return PolarsFrame(df)
    raise TypeError(f"unsupported frame backend: {type(df).__name__}")
