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


@runtime_checkable
class Frame(Protocol):
    @property
    def columns(self) -> list[str]: ...
    @property
    def height(self) -> int: ...
    @property
    def native(self) -> Any: ...
    def column(self, name: str) -> Column: ...


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


def to_frame(native: Any) -> Frame:
    if isinstance(native, PolarsFrame):
        return native
    if isinstance(native, pl.DataFrame):
        return PolarsFrame(native)
    raise TypeError(
        f"to_frame() expects a polars.DataFrame (or PolarsFrame); got {type(native)!r}"
    )
