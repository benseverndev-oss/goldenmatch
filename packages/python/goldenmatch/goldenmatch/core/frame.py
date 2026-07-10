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

import os
import sys
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from goldenmatch._polars_lazy import pl

_VALID_FRAME_BACKENDS = ("polars", "arrow")


def resolve_frame_backend() -> str:
    """Resolve the ``GOLDENMATCH_FRAME`` env var to a Frame backend name.

    Reads ``GOLDENMATCH_FRAME`` (default ``"polars"``), stripped and
    lowercased. Valid values are ``"polars"`` (default, byte-identical
    behavior) and ``"arrow"`` (routes file ingest through pyarrow -- see
    ``core/ingest.py::load_file``).

    Raises:
        ValueError: if the env var is set to anything else, naming the bad
            value and the valid options.
    """
    raw = os.environ.get("GOLDENMATCH_FRAME", "polars").strip().lower()
    if raw not in _VALID_FRAME_BACKENDS:
        raise ValueError(
            f"Invalid GOLDENMATCH_FRAME={raw!r}; valid options are "
            f"{', '.join(sorted(_VALID_FRAME_BACKENDS))!r}"
        )
    return raw


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
    def derive_block_key(
        self, fields: Sequence[str], transforms: Sequence[str], sep: str = "||"
    ) -> Column: ...
    def derive_transformed_column(self, field: str, transforms: Sequence[str]) -> Column: ...
    def utf8_values(self, field: str) -> list[str | None]: ...


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

    def derive_block_key(
        self, fields: Sequence[str], transforms: Sequence[str], sep: str = "||"
    ) -> PolarsColumn:
        # Byte-identical by construction: delegates to the pipeline's own
        # _build_block_key_expr over a Utf8 pre-cast frame (the fused prep's
        # frame-wide cast; the map_elements fallback branch relies on it).
        from types import SimpleNamespace

        from goldenmatch.core.blocker import _build_block_key_expr

        key_cfg = SimpleNamespace(fields=list(fields), transforms=list(transforms))
        df = self._df.with_columns([pl.col(f).cast(pl.Utf8) for f in fields])
        s = df.lazy().select(_build_block_key_expr(key_cfg)).collect().get_column("__block_key__")
        return PolarsColumn(s)

    def derive_transformed_column(self, field: str, transforms: Sequence[str]) -> PolarsColumn:
        # Cast-then-chain (op contract): the same derivation
        # scorer._get_transformed_values performs on the fused prep's pre-cast
        # frame -- native expr chain when fully expressible, else per-value
        # apply_transforms on the cast strings (nulls preserved). The list
        # round-trip through pl.Series mirrors the fused caller exactly.
        from goldenmatch.core.matchkey import _try_native_chain
        from goldenmatch.utils.transforms import apply_transforms

        df = self._df.with_columns(pl.col(field).cast(pl.Utf8))
        chain = list(transforms)
        native = _try_native_chain(field, chain) if chain else None
        if native is not None:
            values = df.select(native.alias("__tmp__"))["__tmp__"].to_list()
        elif chain:
            values = [
                apply_transforms(v, chain) if v is not None else None
                for v in df[field].to_list()
            ]
        else:
            values = df[field].to_list()
        return PolarsColumn(pl.Series(field, values, dtype=pl.Utf8))

    def utf8_values(self, field: str) -> list[str | None]:
        return self._df[field].cast(pl.Utf8).to_list()


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
        import pyarrow as pa
        import pyarrow.compute as pc

        if pa.types.is_null(self._col.type):
            # count_distinct has no kernel for null()-typed columns (what
            # type-inference yields for all-null or untyped-empty data).
            # Polars: 1 distinct value (null) when non-empty, 0 when empty.
            return 1 if len(self._col) > 0 else 0
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

    def derive_block_key(
        self, fields: Sequence[str], transforms: Sequence[str], sep: str = "||"
    ) -> ArrowColumn:
        from goldenmatch.core import arrow_derive

        arrs = [self._tbl.column(f) for f in fields]
        return ArrowColumn(arrow_derive.block_key(arrs, list(transforms), sep=sep))

    def derive_transformed_column(self, field: str, transforms: Sequence[str]) -> ArrowColumn:
        from goldenmatch.core import arrow_derive

        return ArrowColumn(arrow_derive.transformed_column(self._tbl.column(field), list(transforms)))

    def utf8_values(self, field: str) -> list[str | None]:
        from goldenmatch.core import arrow_derive

        return arrow_derive.cast_utf8(self._tbl.column(field)).to_pylist()


def to_frame(obj: Any) -> Frame:
    """Idempotent coercion: raw ``pl.DataFrame``/``pa.Table``, a
    ``dict[str, pa.Array]`` (the fused-FFI column shape), or a ``Frame``.

    Ordering matters: the Arrow branches run BEFORE the ``pl.DataFrame``
    isinstance, and that check only fires when polars is already imported --
    the ``_LazyPolars`` proxy would otherwise import polars just to answer the
    isinstance, defeating the arrow lane's polars-free guarantee (a real
    ``pl.DataFrame`` input implies polars is in ``sys.modules`` already).
    """
    if isinstance(obj, (PolarsFrame, ArrowFrame)):
        return obj

    import pyarrow as pa

    if isinstance(obj, pa.Table):
        return ArrowFrame(obj)
    if isinstance(obj, dict):
        return ArrowFrame(pa.table(obj))
    if "polars" in sys.modules and isinstance(obj, pl.DataFrame):
        return PolarsFrame(obj)
    raise TypeError(
        f"to_frame expects a polars DataFrame, pyarrow Table, dict of arrow "
        f"columns, or Frame, got {type(obj)!r}"
    )
