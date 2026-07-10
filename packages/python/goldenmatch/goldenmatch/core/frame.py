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
    def unique(self) -> Column: ...
    def max(self) -> Any: ...
    def to_numpy(self) -> Any: ...


@runtime_checkable
class Frame(Protocol):
    # W2b relational-op contracts (pinned by tests/test_frame_relational_ops.py):
    # - Join row ORDER is NOT part of any join op's contract; callers that need
    #   an order sort explicitly. Null keys never match (both engines' default).
    # - `sort` is stable, ascending, nulls FIRST (Polars' default; the Arrow
    #   backend passes null_placement="at_start" to match).
    # - `partition_by_key` assumes the frame is ALREADY key-sorted (its one
    #   engine call site sorts first) and yields groups in encounter order.
    # - `filter_mask` drops null-mask rows (both engines' default).
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
    def self_join_on(self, key: str, id_col: str, suffix: str = "_right") -> Frame: ...
    def join_inner(
        self,
        other: Frame,
        on: str | None = None,
        left_on: str | None = None,
        right_on: str | None = None,
        suffix: str = "_right",
    ) -> Frame: ...
    def join_left(self, other: Frame, on: str, suffix: str = "_right") -> Frame: ...
    def rename(self, mapping: dict[str, str]) -> Frame: ...
    def drop(self, cols: Sequence[str]) -> Frame: ...
    def filter_mask(self, mask: Column) -> Frame: ...
    def filter_valid_key(self, col: str) -> Frame: ...
    def group_len(self, keys: Sequence[str]) -> Frame: ...
    def partition_by_key(self, key: str) -> list[tuple[Any, Frame]]: ...
    def sort(self, keys: Sequence[str]) -> Frame: ...
    def slice(self, offset: int, length: int) -> Frame: ...
    def take_rows(self, indices: Sequence[int]) -> Frame: ...


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

    def unique(self) -> PolarsColumn:
        # First-appearance order pinned (raw call sites use order-insensitive
        # .unique(); the seam pins maintain_order so both backends agree).
        return PolarsColumn(self._s.unique(maintain_order=True))

    def max(self) -> Any:
        return self._s.max()

    def to_numpy(self) -> Any:
        return self._s.to_numpy()


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
                apply_transforms(v, chain) if v is not None else None for v in df[field].to_list()
            ]
        else:
            values = df[field].to_list()
        return PolarsColumn(pl.Series(field, values, dtype=pl.Utf8))

    def utf8_values(self, field: str) -> list[str | None]:
        return self._df[field].cast(pl.Utf8).to_list()

    # -- W2b relational ops (each delegates to the exact Polars call the
    # engine call site uses today; the call site is named per op) ----------

    def self_join_on(self, key: str, id_col: str, suffix: str = "_right") -> PolarsFrame:
        # scorer._find_exact_match_ids (scorer.py ~391): inner self-join on the
        # matchkey column + `<` filter keeps each unordered pair once.
        joined = self._df.join(self._df, on=key, suffix=suffix).filter(
            pl.col(id_col) < pl.col(f"{id_col}{suffix}")
        )
        return PolarsFrame(joined)

    def join_inner(
        self,
        other: Frame,
        on: str | None = None,
        left_on: str | None = None,
        right_on: str | None = None,
        suffix: str = "_right",
    ) -> PolarsFrame:
        # golden.build_golden_records_from_frames (golden.py ~1302/~1306).
        if on is not None:
            joined = self._df.join(other.native, on=on, how="inner", suffix=suffix)
        else:
            joined = self._df.join(
                other.native, left_on=left_on, right_on=right_on, how="inner", suffix=suffix
            )
        return PolarsFrame(joined)

    def join_left(self, other: Frame, on: str, suffix: str = "_right") -> PolarsFrame:
        # scorer columnar cross-source filter (scorer.py ~1377/~1846).
        return PolarsFrame(self._df.join(other.native, on=on, how="left", suffix=suffix))

    def rename(self, mapping: dict[str, str]) -> PolarsFrame:
        return PolarsFrame(self._df.rename(dict(mapping)))

    def drop(self, cols: Sequence[str]) -> PolarsFrame:
        return PolarsFrame(self._df.drop(list(cols)))

    def filter_mask(self, mask: Column) -> PolarsFrame:
        # Null mask entries drop the row (Polars .filter default).
        return PolarsFrame(self._df.filter(pl.Series(mask.to_list(), dtype=pl.Boolean)))

    def filter_valid_key(self, col: str) -> PolarsFrame:
        # The blocker sentinel guard VERBATIM (blocker.py ~362-368): drop null
        # keys and the stringified-missing sentinels; keep "" (a real value --
        # the PR #390 regression). `col` must already be a string column.
        return PolarsFrame(
            self._df.filter(
                pl.col(col).is_not_null()
                & ~pl.col(col).str.strip_chars().str.to_lowercase().is_in(["nan", "null", "none"])
            )
        )

    def group_len(self, keys: Sequence[str]) -> PolarsFrame:
        # blocker._fast_static_block_sizes / auto-split (blocker.py ~135/~710).
        # Output column is named "len" (pl.len()'s default); dtype is NOT part
        # of the contract (Polars UInt32 vs Arrow int64) -- callers read values.
        return PolarsFrame(self._df.group_by(list(keys)).agg(pl.len()))

    def partition_by_key(self, key: str) -> list[tuple[Any, PolarsFrame]]:
        # golden survivorship partition (golden.py ~914); input pre-sorted.
        parts = self._df.partition_by(key, maintain_order=True, include_key=True)
        return [(p[key][0], PolarsFrame(p)) for p in parts]

    def sort(self, keys: Sequence[str]) -> PolarsFrame:
        # Stable, ascending, nulls first (Polars defaults + maintain_order).
        return PolarsFrame(self._df.sort(list(keys), maintain_order=True))

    def slice(self, offset: int, length: int) -> PolarsFrame:
        return PolarsFrame(self._df.slice(offset, length))

    def take_rows(self, indices: Sequence[int]) -> PolarsFrame:
        # blocker ANN/canopy positional selection (blocker.py ~574/~881).
        return PolarsFrame(self._df[list(indices)])


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

    def unique(self) -> ArrowColumn:
        import pyarrow.compute as pc

        # pc.unique preserves first-appearance order, matching the pinned
        # PolarsColumn.unique(maintain_order=True) contract.
        return ArrowColumn(pc.unique(self._col))

    def max(self) -> Any:
        import pyarrow.compute as pc

        return pc.max(self._col).as_py()

    def to_numpy(self) -> Any:
        arr = self._col
        if hasattr(arr, "combine_chunks"):
            arr = arr.combine_chunks()
        return arr.to_numpy(zero_copy_only=False)


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

        return ArrowColumn(
            arrow_derive.transformed_column(self._tbl.column(field), list(transforms))
        )

    def utf8_values(self, field: str) -> list[str | None]:
        from goldenmatch.core import arrow_derive

        return arrow_derive.cast_utf8(self._tbl.column(field)).to_pylist()

    # -- W2b relational ops (Acero/pyarrow.compute twins; every semantic
    # delta vs Polars is normalized HERE, pinned by the fixtures) -----------

    def self_join_on(self, key: str, id_col: str, suffix: str = "_right") -> ArrowFrame:
        import pyarrow.compute as pc

        joined = self._tbl.join(self._tbl, keys=key, join_type="inner", right_suffix=suffix)
        mask = pc.less(joined.column(id_col), joined.column(f"{id_col}{suffix}"))
        return ArrowFrame(joined.filter(mask))

    def join_inner(
        self,
        other: Frame,
        on: str | None = None,
        left_on: str | None = None,
        right_on: str | None = None,
        suffix: str = "_right",
    ) -> ArrowFrame:
        if on is not None:
            return ArrowFrame(
                self._tbl.join(other.native, keys=on, join_type="inner", right_suffix=suffix)
            )
        assert left_on is not None and right_on is not None
        joined = self._tbl.join(
            other.native,
            keys=left_on,
            right_keys=right_on,
            join_type="inner",
            right_suffix=suffix,
        )
        return ArrowFrame(joined)

    def join_left(self, other: Frame, on: str, suffix: str = "_right") -> ArrowFrame:
        return ArrowFrame(
            self._tbl.join(other.native, keys=on, join_type="left outer", right_suffix=suffix)
        )

    def rename(self, mapping: dict[str, str]) -> ArrowFrame:
        new_names = [mapping.get(c, c) for c in self._tbl.column_names]
        return ArrowFrame(self._tbl.rename_columns(new_names))

    def drop(self, cols: Sequence[str]) -> ArrowFrame:
        return ArrowFrame(self._tbl.drop_columns(list(cols)))

    def filter_mask(self, mask: Column) -> ArrowFrame:
        import pyarrow as pa

        # null_selection_behavior="drop" is pyarrow's default and matches
        # Polars .filter (null mask row -> dropped); passed explicitly anyway.
        mask_arr = pa.array(mask.to_list(), type=pa.bool_())
        return ArrowFrame(self._tbl.filter(mask_arr, null_selection_behavior="drop"))

    def filter_valid_key(self, col: str) -> ArrowFrame:
        import pyarrow as pa
        import pyarrow.compute as pc

        c = self._tbl.column(col)
        normalized = pc.utf8_lower(pc.utf8_trim_whitespace(c))
        sentinel = pc.is_in(normalized, value_set=pa.array(["nan", "null", "none"]))
        keep = pc.and_kleene(pc.is_valid(c), pc.invert(sentinel))
        return ArrowFrame(self._tbl.filter(keep, null_selection_behavior="drop"))

    def group_len(self, keys: Sequence[str]) -> ArrowFrame:
        grouped = self._tbl.group_by(list(keys)).aggregate([([], "count_all")])
        new_names = ["len" if c == "count_all" else c for c in grouped.column_names]
        return ArrowFrame(grouped.rename_columns(new_names))

    def partition_by_key(self, key: str) -> list[tuple[Any, ArrowFrame]]:
        # Input is pre-sorted by `key` (op contract): zero-copy run slicing.
        vals = self._tbl.column(key).to_pylist()
        out: list[tuple[Any, ArrowFrame]] = []
        start = 0
        for i in range(1, len(vals) + 1):
            if i == len(vals) or vals[i] != vals[start]:
                out.append((vals[start], ArrowFrame(self._tbl.slice(start, i - start))))
                start = i
        return out

    def sort(self, keys: Sequence[str]) -> ArrowFrame:
        import pyarrow.compute as pc

        # Stable; nulls FIRST to match Polars' default (pyarrow defaults to
        # at_end -- a pinned divergence).
        idx = pc.sort_indices(
            self._tbl,
            sort_keys=[(k, "ascending") for k in keys],
            null_placement="at_start",
        )
        return ArrowFrame(self._tbl.take(idx))

    def slice(self, offset: int, length: int) -> ArrowFrame:
        return ArrowFrame(self._tbl.slice(offset, length))

    def take_rows(self, indices: Sequence[int]) -> ArrowFrame:
        return ArrowFrame(self._tbl.take(list(indices)))


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


# -- W2b constructors (module-level: they make a Frame, they don't act on one)

# The pinned dtype vocabulary for frame_from_columns/empty_frame schemas.
# Deliberately tiny: exactly what the engine's frame-construction call sites
# use (cluster.py assignment/metadata buffers, scorer pair streams).
_SCHEMA_DTYPES = ("int64", "uint32", "float64", "utf8", "bool")


def _polars_dtype(name: str) -> Any:
    return {
        "int64": pl.Int64,
        "uint32": pl.UInt32,
        "float64": pl.Float64,
        "utf8": pl.Utf8,
        "bool": pl.Boolean,
    }[name]


def _arrow_dtype(name: str) -> Any:
    import pyarrow as pa

    return {
        "int64": pa.int64(),
        "uint32": pa.uint32(),
        "float64": pa.float64(),
        "utf8": pa.large_string(),  # Polars exports Utf8 as LargeUtf8
        "bool": pa.bool_(),
    }[name]


def _check_schema(schema: dict[str, str]) -> None:
    bad = [f"{k}={v}" for k, v in schema.items() if v not in _SCHEMA_DTYPES]
    if bad:
        raise ValueError(f"unsupported schema dtype(s) {bad}; supported: {_SCHEMA_DTYPES}")


def concat_frames(frames: Sequence[Frame]) -> Frame:
    """Vertical concat, schema-checked by the underlying engine. All frames
    must share a backend (mixing indicates a caller bug, not a coercion
    opportunity)."""
    if not frames:
        raise ValueError("concat_frames requires at least one frame")
    if all(isinstance(f, PolarsFrame) for f in frames):
        return PolarsFrame(pl.concat([f.native for f in frames], how="vertical"))
    if all(isinstance(f, ArrowFrame) for f in frames):
        import pyarrow as pa

        return ArrowFrame(pa.concat_tables([f.native for f in frames]))
    raise TypeError("concat_frames requires all frames on the same backend")


def frame_from_columns(
    data: dict[str, Any], schema: dict[str, str], backend: str | None = None
) -> Frame:
    """Build a Frame from name -> list/numpy/arrow buffers with an EXPLICIT
    schema (dtype vocabulary: ``_SCHEMA_DTYPES``). ``backend`` defaults to
    ``resolve_frame_backend()``."""
    _check_schema(schema)
    b = backend if backend is not None else resolve_frame_backend()
    if b == "polars":
        return PolarsFrame(
            pl.DataFrame(data, schema={k: _polars_dtype(v) for k, v in schema.items()})
        )
    import pyarrow as pa

    arrays = {}
    for name, buf in data.items():
        typ = _arrow_dtype(schema[name])
        if isinstance(buf, (pa.Array, pa.ChunkedArray)):
            arrays[name] = buf.cast(typ)
        else:
            arrays[name] = pa.array(buf, type=typ)
    return ArrowFrame(pa.table(arrays))


def empty_frame(schema: dict[str, str], backend: str | None = None) -> Frame:
    """A zero-row Frame with the given schema (same dtype vocabulary)."""
    return frame_from_columns({k: [] for k in schema}, schema, backend=backend)
