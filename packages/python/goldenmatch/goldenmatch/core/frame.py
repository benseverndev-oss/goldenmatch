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


def _semantic_dtype_name(dtype_str: str) -> str:
    """Cross-backend dtype tag: {text, numeric, date, bool, unknown}.

    Polars spells Float64/Int64/String/Date/Boolean; Arrow spells
    double/int64/large_string/date32[day]/bool. Raw substring checks like
    `"float" in str(dtype)` misclassify Arrow's "double" as unknown -- this
    table is the one normalization point (W3a, reviewer finding)."""
    d = dtype_str.lower()
    if any(t in d for t in ("utf", "str", "string")):
        return "text"
    if any(t in d for t in ("int", "float", "double", "decimal", "uint")):
        return "numeric"
    if any(t in d for t in ("date", "time")):
        return "date"
    if "bool" in d:
        return "bool"
    return "unknown"


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
    # W3a reduction ops (contracts in tests/test_frame_relational_ops.py):
    # value_counts_desc INCLUDES nulls as a countable value (polars parity)
    # and pins count-desc + stringified-value tiebreak ordering; std is
    # ddof=1; semantic_dtype normalizes cross-backend dtype naming (Arrow
    # says "double" where Polars says Float64 -- raw str(dtype) checks
    # misclassify floats as unknown on arrow).
    def drop_nulls(self) -> Column: ...
    def cast_str(self, strict: bool = True) -> Column: ...
    def fill_null(self, value: Any) -> Column: ...
    def sum(self) -> Any: ...
    def mean(self) -> Any: ...
    def min(self) -> Any: ...
    def std(self) -> Any: ...
    def value_counts_desc(self) -> list[tuple[Any, int]]: ...
    def str_len_chars(self) -> Column: ...
    def blank_count(self) -> int: ...
    def semantic_dtype(self) -> str: ...


# W4a: map_column's "no default" marker -- None is a legitimate default value.
_MAP_RAISE: Any = object()


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
    # W2c ops (columnar spine port; contracts pinned in
    # tests/test_frame_relational_ops.py -- notably: filter_ne_cols null ->
    # DROP (columnar-engine parity, NOT the list path's dict.get semantics);
    # filter_nonblank_key includes the strict=False Utf8 cast and DROPS "";
    # map_column RAISES on unmapped (replace_strict twin); apply_weak_quality
    # reproduces cluster.py Step-3's when/then including the null-condition
    # fall-through-to-strong.
    def select(self, cols: Sequence[str]) -> Frame: ...
    def filter_eq(self, col: str, value: Any) -> Frame: ...
    def filter_not_in(self, col: str, values: Sequence[Any]) -> Frame: ...
    def filter_ne_cols(self, a: str, b: str) -> Frame: ...
    def filter_nonblank_key(self, col: str) -> Frame: ...
    def filter_target_split(self, a: str, b: str, values: Sequence[Any]) -> Frame: ...
    def with_fill_null(self, cols: Sequence[str], value: Any) -> Frame: ...
    def map_column(
        self, src: str, dst: str, mapping: dict, dtype: str = "int64", default: Any = _MAP_RAISE
    ) -> Frame: ...
    def apply_weak_quality(self, weak_threshold: float) -> Frame: ...
    def select_eligible_clusters(self) -> Frame: ...
    # W2d ops: with_column attaches a derived Column; group_partitions is
    # HASH-grouped (first-appearance order, no pre-sort requirement -- unlike
    # partition_by_key, whose adjacent-run slicing on unsorted input would
    # silently split a block). Null keys form a group; callers skip explicitly.
    def with_column(self, name: str, col: Column) -> Frame: ...
    def with_literal_column(self, name: str, value: Any) -> Frame: ...
    def group_partitions(self, key: str) -> list[tuple[Any, Frame]]: ...
    # W3a controller/profiling ops. `sample`: statistical contract, NOT
    # byte -- polars' RNG is not reproducible on arrow; same n,
    # deterministic per (seed, backend), no duplicates, n>height raises.
    # Once arrow flows past ingest (W5) the differential harness's
    # controller expectations become per-backend BY DESIGN.
    def sample(self, n: int, seed: int | None = None, shuffle: bool = False) -> Frame: ...
    def with_row_index(self, name: str = "__row__") -> Frame: ...
    def head(self, n: int) -> Frame: ...
    def joint_n_unique(self, cols: Sequence[str]) -> int: ...
    def group_nunique(self, key: str, value: str) -> Frame: ...
    def coverage_ratio(self, pass_field_lists: Sequence[Sequence[str]]) -> float: ...
    def distinct_row_count(self) -> int: ...
    # W2e-1: one field's standardizer chain (apply_standardization's
    # per-column derivation as a seam op; `address`/plugins fall back to the
    # pure-Python STANDARDIZERS oracle on the arrow backend).
    def derive_standardized_column(self, field: str, std_names: Sequence[str]) -> Column: ...
    # W2e-2: matchkey composite (PER-FIELD transform chains, unlike
    # derive_block_key's shared chain; the non-native fallback runs over RAW
    # values -- matchkey's map_elements has no Utf8 pre-cast) + the derived-NE
    # space-join (fill_null("") means it never null-propagates).
    def derive_matchkey(
        self, fields_with_chains: Sequence[tuple[str, Sequence[str]]], sep: str = "||"
    ) -> Column: ...
    def derive_ne_joined(self, fields: Sequence[str]) -> Column: ...
    # W4a tail/distributed ops (fixtures: tests/test_frame_w4_ops.py). Probed
    # semantics: filter_in DROPS null rows on both backends (polars null mask,
    # pc.is_in False); with_pair_canonical min/max_horizontal SKIP nulls;
    # unique_by result ORDER is engine-defined (SET contract; keep refers to
    # input order); with_group_len_over counts null keys as a group.
    def filter_in(self, col: str, values: Sequence[Any]) -> Frame: ...
    def with_row_index_int64(self, name: str, offset: int = 0) -> Frame: ...
    def with_int64_offset(self, col: str, offset: int) -> Frame: ...
    def with_group_len_over(self, key: str, name: str) -> Frame: ...
    def with_group_min_over(self, key: str, value: str, name: str) -> Frame: ...
    def with_pair_canonical(self, a: str, b: str) -> Frame: ...
    def unique_by(self, subset: Sequence[str], keep: str = "first") -> Frame: ...
    def with_mod_column(self, src: str, n: int, name: str) -> Frame: ...
    # W4e-2 clustering-kernel ops. select_cast projects (src, dtype, alias)
    # triples in order (dtype from the _SCHEMA_DTYPES vocabulary; None = no
    # cast). group_min = group_by(key).agg(min(value)) frame (row order
    # engine-defined, SET contract). with_gt_column derives a bool col.
    def group_min(self, key: str, value: str) -> Frame: ...
    def select_cast(self, spec: Sequence[tuple[str, str | None, str]]) -> Frame: ...
    def with_gt_column(self, src: str, threshold: Any, name: str) -> Frame: ...


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

    # -- W3a reductions (exact-delegation; contracts in the fixtures) ------

    def drop_nulls(self) -> PolarsColumn:
        return PolarsColumn(self._s.drop_nulls())

    def cast_str(self, strict: bool = True) -> PolarsColumn:
        return PolarsColumn(self._s.cast(pl.Utf8, strict=strict))

    def fill_null(self, value: Any) -> PolarsColumn:
        return PolarsColumn(self._s.fill_null(value))

    def sum(self) -> Any:
        return self._s.sum()

    def mean(self) -> Any:
        return self._s.mean()

    def min(self) -> Any:
        return self._s.min()

    def std(self) -> Any:
        return self._s.std()  # ddof=1, polars default (fixture-pinned)

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        # Includes nulls as a value (polars parity); ordering imposed HERE
        # (polars value_counts has no order guarantee): count desc, then
        # stringified value (None first) for cross-backend determinism.
        vc = self._s.value_counts()
        pairs = list(zip(vc[self._s.name].to_list(), vc["count"].to_list()))
        pairs.sort(key=lambda t: (-t[1], t[0] is not None, str(t[0])))
        return pairs

    def str_len_chars(self) -> PolarsColumn:
        return PolarsColumn(self._s.str.len_chars())

    def blank_count(self) -> int:
        # profiler.py:131 shape: non-null values whose strip() == "".
        return int((self._s.drop_nulls().str.strip_chars() == "").sum())

    def semantic_dtype(self) -> str:
        return _semantic_dtype_name(str(self._s.dtype))


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

    # -- W2c ops (each delegates to the exact engine call, cited per op) ----

    def select(self, cols: Sequence[str]) -> PolarsFrame:
        return PolarsFrame(self._df.select(list(cols)))

    def filter_eq(self, col: str, value: Any) -> PolarsFrame:
        # cluster.py ~651: per-oversized-cluster member extraction.
        return PolarsFrame(self._df.filter(pl.col(col) == value))

    def filter_not_in(self, col: str, values: Sequence[Any]) -> PolarsFrame:
        # cluster.py ~706-707: drop split ORIGINAL cluster rows.
        return PolarsFrame(self._df.filter(~pl.col(col).is_in(list(values))))

    def filter_ne_cols(self, a: str, b: str) -> PolarsFrame:
        # scorer.py ~1381/~1850 cross-source filter. NULL comparison -> null
        # mask -> row DROPS (columnar-engine parity; the list path's
        # dict.get() would KEEP a one-sided unknown -- unreachable in-pipeline
        # because source_lookup is total; do not "fix" this to keep).
        return PolarsFrame(self._df.filter(pl.col(a) != pl.col(b)))

    def filter_nonblank_key(self, col: str) -> PolarsFrame:
        # scorer.py ~385-388 blank-exclusion (DQbench T3): drop null AND
        # blank/whitespace-only. strict=False cast is part of the contract
        # (non-string keys stringify; uncastable -> null -> drops). OPPOSITE
        # of filter_valid_key re "".
        return PolarsFrame(
            self._df.filter(
                pl.col(col).is_not_null()
                & (pl.col(col).cast(pl.Utf8, strict=False).str.strip_chars() != "")
            )
        )

    def filter_target_split(self, a: str, b: str, values: Sequence[Any]) -> PolarsFrame:
        # scorer.py ~1986-1990 VERBATIM: keep pairs where EXACTLY ONE endpoint
        # is a target (Int64 series, matching _filter_target_ids_df).
        s = pl.Series("__t__", list(values), dtype=pl.Int64)
        return PolarsFrame(self._df.filter(pl.col(a).is_in(s) != pl.col(b).is_in(s)))

    def with_fill_null(self, cols: Sequence[str], value: Any) -> PolarsFrame:
        # cluster.py ~589-592: coalesce native-bridge null edges.
        return PolarsFrame(self._df.with_columns([pl.col(c).fill_null(value) for c in cols]))

    def map_column(
        self, src: str, dst: str, mapping: dict, dtype: str = "int64", default: Any = _MAP_RAISE
    ) -> PolarsFrame:
        # cluster.py ~1151-1152: tag pairs with their cluster id.
        # replace_strict RAISES on unmapped source values (contract). W4a:
        # optional default= (distributed/pipeline.py:411 remap-with-sentinel);
        # PROBED: polars maps null -> default too (not passthrough).
        if default is _MAP_RAISE:
            expr = pl.col(src).replace_strict(mapping)
        else:
            expr = pl.col(src).replace_strict(mapping, default=default)
        return PolarsFrame(self._df.with_columns(expr.alias(dst)))

    def apply_weak_quality(self, weak_threshold: float) -> PolarsFrame:
        # cluster.py Step-3 (~718-729) VERBATIM: quality recompute (split rows
        # pass through untouched; weak = size>1 and edge-gap > threshold,
        # strict >) then 0.7 confidence damp on weak. Null conditions fall
        # through to "strong" (Polars when() treats null as false).
        df = self._df.with_columns(
            pl.when(pl.col("quality") == "split")
            .then(pl.col("quality"))
            .when(
                (pl.col("size") > 1) & ((pl.col("avg_edge") - pl.col("min_edge")) > weak_threshold)
            )
            .then(pl.lit("weak"))
            .otherwise(pl.lit("strong"))
            .alias("quality"),
        ).with_columns(
            pl.when(pl.col("quality") == "weak")
            .then(pl.col("confidence") * 0.7)
            .otherwise(pl.col("confidence"))
            .alias("confidence"),
        )
        return PolarsFrame(df)

    def select_eligible_clusters(self) -> PolarsFrame:
        # golden.py ~1293-1297 VERBATIM: multi-member, not oversized. The
        # parentheses are load-bearing (& binds tighter than >).
        return PolarsFrame(
            self._df.filter((pl.col("size") > 1) & ~pl.col("oversized")).select("cluster_id")
        )

    def with_column(self, name: str, col: Column) -> PolarsFrame:
        # blocker.py auto-split key attach (~710-712).
        s = col._s if isinstance(col, PolarsColumn) else pl.Series(name, col.to_list())  # noqa: SLF001
        return PolarsFrame(self._df.with_columns(s.alias(name)))

    def with_literal_column(self, name: str, value: Any) -> PolarsFrame:
        # ingest.py:191's `__source__` tag.
        return PolarsFrame(self._df.with_columns(pl.lit(value).alias(name)))

    def group_partitions(self, key: str) -> list[tuple[Any, PolarsFrame]]:
        # blocker.py:373-375's group_by iteration. partition_by with
        # maintain_order is a DETERMINISTIC refinement of the raw
        # nondeterministic group_by order (blocks are an unordered set
        # downstream: thread-pool scored, pairs canonicalized).
        parts = self._df.partition_by(key, maintain_order=True, include_key=True)
        return [(p[key][0], PolarsFrame(p)) for p in parts]

    # -- W3a controller/profiling ops ---------------------------------------

    def sample(self, n: int, seed: int | None = None, shuffle: bool = False) -> PolarsFrame:
        # Statistical contract (see Frame protocol note): polars rows are the
        # per-backend reference; n>height raises (polars ShapeError).
        return PolarsFrame(self._df.sample(n=n, seed=seed, shuffle=shuffle))

    def with_row_index(self, name: str = "__row__") -> PolarsFrame:
        return PolarsFrame(self._df.with_row_index(name))

    def head(self, n: int) -> PolarsFrame:
        return PolarsFrame(self._df.head(n))

    def joint_n_unique(self, cols: Sequence[str]) -> int:
        # autoconfig:3203 composite cardinality (null combos count).
        return int(self._df.select(list(cols)).n_unique())

    def group_nunique(self, key: str, value: str) -> PolarsFrame:
        # _source_disjoint shape: rows where EITHER col is null are DROPPED
        # (frame-level drop_nulls on the 2-col selection) before grouping.
        g = (
            self._df.select([key, value])
            .drop_nulls()
            .group_by(key)
            .agg(pl.col(value).n_unique().alias("n_unique"))
        )
        return PolarsFrame(g)

    def coverage_ratio(self, pass_field_lists: Sequence[Sequence[str]]) -> float:
        # _union_coverage:1436-1451 VERBATIM: fraction of rows covered by at
        # least one pass whose fields are ALL non-null. Missing column ->
        # that pass covers nothing; empty pass list -> 0.0; an empty
        # fields-list INSIDE a pass covers everything (all-True fold,
        # unreachable today, pinned by fixture); height==0 -> 0.0.
        if self._df.height == 0:
            return 0.0
        covered = pl.repeat(False, self._df.height, eager=True)
        for fields in pass_field_lists:
            present = pl.repeat(True, self._df.height, eager=True)
            ok = True
            for f in fields:
                if f not in self._df.columns:
                    ok = False
                    break
                present = present & self._df[f].is_not_null()
            if not ok:
                continue
            covered = covered | present
        return float(covered.sum() / self._df.height)

    def distinct_row_count(self) -> int:
        return int(self._df.unique().height)

    def derive_standardized_column(self, field: str, std_names: Sequence[str]) -> PolarsColumn:
        # Byte-identical to apply_standardization's three per-column branches
        # (standardize.py:443-501): native builders as one fused expr chain,
        # any non-native tail via ONE chained map_elements UDF -- including
        # the mixed-chain REORDERING quirk (native first, then non-native).
        from goldenmatch.core.standardize import _NATIVE_STANDARDIZERS, get_standardizer

        expr = pl.col(field).cast(pl.Utf8)
        for name in [n for n in std_names if n in _NATIVE_STANDARDIZERS]:
            expr = _NATIVE_STANDARDIZERS[name](expr)  # type: ignore[operator]
        non_native = [n for n in std_names if n not in _NATIVE_STANDARDIZERS]
        if non_native:
            funcs = [get_standardizer(n) for n in non_native]

            def _chained(val: Any, _funcs: Any = funcs) -> Any:
                for fn in _funcs:
                    val = fn(val)
                return val

            # noqa: GM-MAP-ELEMENTS: non-native standardizer tail needs per-row Python (mirrors apply_standardization)
            expr = expr.map_elements(_chained, return_dtype=pl.Utf8)
        return PolarsColumn(self._df.select(expr.alias("__std__"))["__std__"])

    def derive_matchkey(
        self, fields_with_chains: Sequence[tuple[str, Sequence[str]]], sep: str = "||"
    ) -> PolarsColumn:
        # build_matchkey_expr's per-field branches VERBATIM (matchkey.py
        # :164-184): native chain when fully expressible, else map_elements
        # over RAW values (NO pre-cast -- the one contract difference vs the
        # fused prep's cast-then-chain), else plain cast; concat_str for
        # multi-field.
        from goldenmatch.core.matchkey import _build_field_expr_native
        from goldenmatch.utils.transforms import apply_transforms

        field_exprs = []
        for f, transforms in fields_with_chains:
            chain = list(transforms)
            if chain:
                native = _build_field_expr_native(f, chain)
                if native is not None:
                    expr = native
                else:
                    # noqa: GM-MAP-ELEMENTS: non-native matchkey transform (soundex/metaphone); mirrors build_matchkey_expr
                    expr = pl.col(f).map_elements(
                        lambda val, _c=chain: apply_transforms(val, _c),
                        return_dtype=pl.Utf8,
                    )
            else:
                expr = pl.col(f).cast(pl.Utf8)
            field_exprs.append(expr)
        out = field_exprs[0] if len(field_exprs) == 1 else pl.concat_str(field_exprs, separator=sep)
        return PolarsColumn(self._df.select(out.alias("__mk__"))["__mk__"])

    def derive_ne_joined(self, fields: Sequence[str]) -> PolarsColumn:
        # precompute_matchkey_transforms' derived-NE join (matchkey.py:381-386).
        expr = pl.concat_str([pl.col(c).cast(pl.Utf8).fill_null("") for c in fields], separator=" ")
        return PolarsColumn(self._df.select(expr.alias("__ne__"))["__ne__"])

    # -- W4a tail/distributed ops --------------------------------------------

    def filter_in(self, col: str, values: Sequence[Any]) -> PolarsFrame:
        # resolve.py:170/918/996, sync.py:994, tui/engine.py:312/338/339.
        return PolarsFrame(self._df.filter(pl.col(col).is_in(list(values))))

    def with_row_index_int64(self, name: str, offset: int = 0) -> PolarsFrame:
        # web/preview.py:189's pl.int_range(0, height, dtype=Int64) + offset.
        return PolarsFrame(
            self._df.with_columns(
                pl.int_range(offset, offset + self._df.height, dtype=pl.Int64).alias(name)
            )
        )

    def with_int64_offset(self, col: str, offset: int) -> PolarsFrame:
        # chunked.py:91-93: (col + offset).cast(Int64) in place.
        return PolarsFrame(self._df.with_columns((pl.col(col) + offset).cast(pl.Int64).alias(col)))

    def with_group_len_over(self, key: str, name: str) -> PolarsFrame:
        # clustering.py ~430: per-row group size window.
        return PolarsFrame(self._df.with_columns(pl.len().over(key).alias(name)))

    def with_group_min_over(self, key: str, value: str, name: str) -> PolarsFrame:
        # clustering.py ~1234: per-row group min window.
        return PolarsFrame(self._df.with_columns(pl.col(value).min().over(key).alias(name)))

    def with_pair_canonical(self, a: str, b: str) -> PolarsFrame:
        # distributed/scoring.py ~560: canonical (min, max) endpoint order.
        return PolarsFrame(
            self._df.with_columns(
                [pl.min_horizontal(a, b).alias(a), pl.max_horizontal(a, b).alias(b)]
            )
        )

    def unique_by(self, subset: Sequence[str], keep: str = "first") -> PolarsFrame:
        # clustering ~1237 / pipeline.py:1970. Result order engine-defined.
        return PolarsFrame(self._df.unique(subset=list(subset), keep=keep))  # type: ignore[arg-type]

    def with_mod_column(self, src: str, n: int, name: str) -> PolarsFrame:
        # distributed/identity_partition.py:100-102 partition tag.
        return PolarsFrame(self._df.with_columns((pl.col(src) % n).alias(name)))

    def group_min(self, key: str, value: str) -> PolarsFrame:
        # distributed/clustering.py:833 per-id min label.
        return PolarsFrame(self._df.group_by(key).agg(pl.col(value).min()))

    def select_cast(self, spec: Sequence[tuple[str, str | None, str]]) -> PolarsFrame:
        # clustering UDF output projections: select(col.cast(dtype).alias(name)).
        exprs = []
        for src, dtype, alias in spec:
            e = pl.col(src)
            if dtype is not None:
                e = e.cast(_polars_dtype(dtype))
            exprs.append(e.alias(alias))
        return PolarsFrame(self._df.select(exprs))

    def with_gt_column(self, src: str, threshold: Any, name: str) -> PolarsFrame:
        # clustering.py:436 oversized flag.
        return PolarsFrame(self._df.with_columns((pl.col(src) > threshold).alias(name)))


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

    # -- W3a reductions (pc twins; contracts in the fixtures) --------------

    def drop_nulls(self) -> ArrowColumn:
        import pyarrow.compute as pc

        return ArrowColumn(pc.drop_null(self._col))

    def cast_str(self, strict: bool = True) -> ArrowColumn:
        from goldenmatch.core import arrow_derive

        # strict is a polars-ism (uncastable -> null when False); the
        # arrow_derive cast handles every dtype in the corpus and never
        # raises for them, so both flavors route through it (fixture-pinned
        # against BOTH strict values on the polars side).
        return ArrowColumn(arrow_derive.cast_utf8(self._col))

    def fill_null(self, value: Any) -> ArrowColumn:
        import pyarrow.compute as pc

        return ArrowColumn(pc.fill_null(self._col, value))

    def sum(self) -> Any:
        import pyarrow.compute as pc

        v = pc.sum(self._col).as_py()
        return v

    def mean(self) -> Any:
        import pyarrow.compute as pc

        return pc.mean(self._col).as_py()

    def min(self) -> Any:
        import pyarrow.compute as pc

        return pc.min(self._col).as_py()

    def std(self) -> Any:
        import pyarrow.compute as pc

        return pc.stddev(self._col, ddof=1).as_py()  # polars ddof=1 parity

    def value_counts_desc(self) -> list[tuple[Any, int]]:
        import pyarrow.compute as pc

        vc = pc.value_counts(self._col)  # includes nulls, like polars
        pairs = [(d["values"], d["counts"]) for d in vc.to_pylist()]
        pairs.sort(key=lambda t: (-t[1], t[0] is not None, str(t[0])))
        return pairs

    def str_len_chars(self) -> ArrowColumn:
        import pyarrow.compute as pc

        return ArrowColumn(pc.utf8_length(self._col))  # codepoints, like polars

    def blank_count(self) -> int:
        import pyarrow.compute as pc

        nonnull = pc.drop_null(self._col)
        return pc.sum(pc.equal(pc.utf8_trim_whitespace(nonnull), "")).as_py() or 0

    def semantic_dtype(self) -> str:
        t = self._col.type if not hasattr(self._col, "chunks") else self._col.type
        return _semantic_dtype_name(str(t))


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

    # -- W2c ops (pc twins; Polars-parity semantics pinned by fixtures) -----

    def _filter_nullable_mask(self, mask: Any) -> ArrowFrame:
        return ArrowFrame(self._tbl.filter(mask, null_selection_behavior="drop"))

    def select(self, cols: Sequence[str]) -> ArrowFrame:
        return ArrowFrame(self._tbl.select(list(cols)))

    def filter_eq(self, col: str, value: Any) -> ArrowFrame:
        import pyarrow.compute as pc

        # null == value -> null -> drop (matches Polars ==).
        return self._filter_nullable_mask(pc.equal(self._tbl.column(col), value))

    def filter_not_in(self, col: str, values: Sequence[Any]) -> ArrowFrame:
        import pyarrow as pa
        import pyarrow.compute as pc

        c = self._tbl.column(col)
        # Polars ~is_in yields null for null input -> row drops; Arrow is_in
        # returns FALSE for nulls, so AND with validity to reproduce the drop.
        keep = pc.and_kleene(
            pc.invert(pc.is_in(c, value_set=pa.array(list(values)))), pc.is_valid(c)
        )
        return self._filter_nullable_mask(keep)

    def filter_ne_cols(self, a: str, b: str) -> ArrowFrame:
        import pyarrow.compute as pc

        # not_equal null-propagates -> drop, same as Polars != (see the
        # PolarsFrame op note re the list path's differing dict semantics).
        return self._filter_nullable_mask(pc.not_equal(self._tbl.column(a), self._tbl.column(b)))

    def filter_nonblank_key(self, col: str) -> ArrowFrame:
        import pyarrow.compute as pc

        from goldenmatch.core import arrow_derive

        c = self._tbl.column(col)
        cast = arrow_derive.cast_utf8(c)  # the strict=False Utf8 cast twin
        nonblank = pc.not_equal(pc.utf8_trim_whitespace(cast), "")
        keep = pc.and_kleene(pc.is_valid(c), nonblank)  # null cast -> null -> drop
        return self._filter_nullable_mask(keep)

    def filter_target_split(self, a: str, b: str, values: Sequence[Any]) -> ArrowFrame:
        import pyarrow as pa
        import pyarrow.compute as pc

        vs = pa.array(list(values))

        def _mask(col_name: str) -> Any:
            c = self._tbl.column(col_name)
            # Preserve null -> null (Polars is_in) so the XOR null-propagates
            # and the row drops; Arrow is_in alone would emit false.
            return pc.if_else(
                pc.is_valid(c), pc.is_in(c, value_set=vs), pa.scalar(None, pa.bool_())
            )

        return self._filter_nullable_mask(pc.not_equal(_mask(a), _mask(b)))

    def with_fill_null(self, cols: Sequence[str], value: Any) -> ArrowFrame:
        import pyarrow.compute as pc

        tbl = self._tbl
        for c in cols:
            idx = tbl.column_names.index(c)
            tbl = tbl.set_column(idx, c, pc.fill_null(tbl.column(c), value))
        return ArrowFrame(tbl)

    def map_column(
        self, src: str, dst: str, mapping: dict, dtype: str = "int64", default: Any = _MAP_RAISE
    ) -> ArrowFrame:
        import pyarrow as pa

        vals = self._tbl.column(src).to_pylist()
        if default is _MAP_RAISE:
            try:
                mapped = [None if v is None else mapping[v] for v in vals]
            except KeyError as e:  # replace_strict twin: unmapped RAISES
                raise ValueError(
                    f"map_column: value {e.args[0]!r} in {src!r} not in mapping"
                ) from e
        else:
            # PROBED polars parity: replace_strict(mapping, default) maps
            # unmapped values AND nulls to the default.
            mapped = [mapping.get(v, default) for v in vals]
        return ArrowFrame(self._tbl.append_column(dst, pa.array(mapped, type=_arrow_dtype(dtype))))

    def apply_weak_quality(self, weak_threshold: float) -> ArrowFrame:
        import pyarrow.compute as pc

        tbl = self._tbl
        q = tbl.column("quality")
        size = tbl.column("size")
        gap = pc.subtract(tbl.column("avg_edge"), tbl.column("min_edge"))
        conf = tbl.column("confidence")

        def _cond(c: Any) -> Any:
            # Polars when() treats a NULL condition as FALSE (falls through);
            # pc.if_else would propagate null -- fill to false to match.
            return pc.fill_null(c, False)

        import pyarrow as pa

        qt = (
            q.type
            if not isinstance(q, pa.ChunkedArray)
            else q.chunk(0).type
            if q.num_chunks
            else pa.large_string()
        )
        weak_lit = pa.scalar("weak", type=qt)
        strong_lit = pa.scalar("strong", type=qt)
        is_split = _cond(pc.equal(q, "split"))
        is_weak = _cond(pc.and_kleene(pc.greater(size, 1), pc.greater(gap, weak_threshold)))
        new_q = pc.if_else(is_split, q, pc.if_else(is_weak, weak_lit, strong_lit))
        new_conf = pc.if_else(_cond(pc.equal(new_q, "weak")), pc.multiply(conf, 0.7), conf)
        qi = tbl.column_names.index("quality")
        tbl = tbl.set_column(qi, "quality", new_q)
        ci = tbl.column_names.index("confidence")
        return ArrowFrame(tbl.set_column(ci, "confidence", new_conf))

    def select_eligible_clusters(self) -> ArrowFrame:
        import pyarrow.compute as pc

        keep = pc.and_kleene(
            pc.greater(self._tbl.column("size"), 1),
            pc.invert(self._tbl.column("oversized")),
        )
        return ArrowFrame(
            self._tbl.filter(keep, null_selection_behavior="drop").select(["cluster_id"])
        )

    def with_column(self, name: str, col: Column) -> ArrowFrame:
        import pyarrow as pa

        arr = col.to_arrow()
        if isinstance(arr, pa.ChunkedArray):
            arr = arr.combine_chunks()
        if name in self._tbl.column_names:
            idx = self._tbl.column_names.index(name)
            return ArrowFrame(self._tbl.set_column(idx, name, arr))
        return ArrowFrame(self._tbl.append_column(name, arr))

    def with_literal_column(self, name: str, value: Any) -> ArrowFrame:
        import pyarrow as pa

        return ArrowFrame(self._tbl.append_column(name, pa.array([value] * self._tbl.num_rows)))

    def group_partitions(self, key: str) -> list[tuple[Any, ArrowFrame]]:
        # Hash-grouped, first-appearance order, correct on UNSORTED input
        # (adjacent-run slicing would split recurring keys). Null keys group.
        vals = self._tbl.column(key).to_pylist()
        groups: dict[Any, list[int]] = {}
        order: list[Any] = []
        for i, v in enumerate(vals):
            if v not in groups:
                groups[v] = []
                order.append(v)
            groups[v].append(i)
        return [(v, ArrowFrame(self._tbl.take(groups[v]))) for v in order]

    # -- W3a controller/profiling ops ---------------------------------------

    def sample(self, n: int, seed: int | None = None, shuffle: bool = False) -> ArrowFrame:
        import random

        if n > self._tbl.num_rows:
            raise ValueError(
                f"cannot take a larger sample ({n}) than the population "
                f"({self._tbl.num_rows}) -- polars ShapeError parity"
            )
        rng = random.Random(seed)
        idx = rng.sample(range(self._tbl.num_rows), n)
        if not shuffle:
            idx.sort()  # polars shuffle=False keeps frame order
        return ArrowFrame(self._tbl.take(idx))

    def with_row_index(self, name: str = "__row__") -> ArrowFrame:
        import pyarrow as pa

        idx = pa.array(range(self._tbl.num_rows), type=pa.uint32())
        return ArrowFrame(self._tbl.add_column(0, name, idx))

    def head(self, n: int) -> ArrowFrame:
        return ArrowFrame(self._tbl.slice(0, n))

    def joint_n_unique(self, cols: Sequence[str]) -> int:
        rows = zip(*(self._tbl.column(c).to_pylist() for c in cols))
        return len(set(rows))

    def group_nunique(self, key: str, value: str) -> ArrowFrame:
        import pyarrow.compute as pc

        sub = self._tbl.select([key, value])
        keep = pc.and_kleene(pc.is_valid(sub.column(key)), pc.is_valid(sub.column(value)))
        sub = sub.filter(keep)  # either-col-null rows DROP (contract)
        grouped = sub.group_by(key).aggregate([(value, "count_distinct")])
        names = ["n_unique" if c == f"{value}_count_distinct" else c for c in grouped.column_names]
        return ArrowFrame(grouped.rename_columns(names))

    def coverage_ratio(self, pass_field_lists: Sequence[Sequence[str]]) -> float:
        import pyarrow as pa
        import pyarrow.compute as pc

        n = self._tbl.num_rows
        if n == 0:
            return 0.0
        covered = pa.array([False] * n, type=pa.bool_())
        for fields in pass_field_lists:
            present = pa.array([True] * n, type=pa.bool_())
            ok = True
            for f in fields:
                if f not in self._tbl.column_names:
                    ok = False
                    break
                present = pc.and_(present, pc.is_valid(self._tbl.column(f)))
            if not ok:
                continue
            covered = pc.or_(covered, present)
        return float((pc.sum(covered).as_py() or 0) / n)

    def distinct_row_count(self) -> int:
        cols = self._tbl.column_names
        rows = zip(*(self._tbl.column(c).to_pylist() for c in cols))
        return len(set(rows))

    def derive_standardized_column(self, field: str, std_names: Sequence[str]) -> ArrowColumn:
        from goldenmatch.core import arrow_derive

        return ArrowColumn(
            arrow_derive.standardized_column(self._tbl.column(field), list(std_names))
        )

    def derive_matchkey(
        self, fields_with_chains: Sequence[tuple[str, Sequence[str]]], sep: str = "||"
    ) -> ArrowColumn:
        from goldenmatch.core import arrow_derive

        pairs = [(self._tbl.column(f), list(t)) for f, t in fields_with_chains]
        return ArrowColumn(arrow_derive.matchkey_composite(pairs, sep=sep))

    def derive_ne_joined(self, fields: Sequence[str]) -> ArrowColumn:
        from goldenmatch.core import arrow_derive

        return ArrowColumn(arrow_derive.ne_joined_column([self._tbl.column(f) for f in fields]))

    # -- W4a tail/distributed ops --------------------------------------------

    def filter_in(self, col: str, values: Sequence[Any]) -> ArrowFrame:
        import pyarrow as pa
        import pyarrow.compute as pc

        # Nones are stripped from the value set: polars drops null rows even
        # when None is listed (probed), while pc.is_in would MATCH them.
        vals = [v for v in values if v is not None]
        if not vals:
            return ArrowFrame(self._tbl.slice(0, 0))
        # pc.is_in: null -> False == polars null-mask DROP (probed).
        mask = pc.is_in(self._tbl.column(col), value_set=pa.array(vals))
        return ArrowFrame(self._tbl.filter(mask))

    def with_row_index_int64(self, name: str, offset: int = 0) -> ArrowFrame:
        import pyarrow as pa

        n = self._tbl.num_rows
        return ArrowFrame(
            self._tbl.append_column(name, pa.array(range(offset, offset + n), type=pa.int64()))
        )

    def with_int64_offset(self, col: str, offset: int) -> ArrowFrame:
        import pyarrow as pa
        import pyarrow.compute as pc

        idx = self._tbl.column_names.index(col)
        arr = pc.cast(pc.add(self._tbl.column(col), offset), pa.int64())
        return ArrowFrame(self._tbl.set_column(idx, col, arr))

    def with_group_len_over(self, key: str, name: str) -> ArrowFrame:
        from collections import Counter

        import pyarrow as pa

        keys = self._tbl.column(key).to_pylist()
        counts = Counter(keys)  # None keys count as a group (polars over parity)
        return ArrowFrame(
            self._tbl.append_column(name, pa.array([counts[k] for k in keys], type=pa.uint32()))
        )

    def with_group_min_over(self, key: str, value: str, name: str) -> ArrowFrame:
        keys = self._tbl.column(key).to_pylist()
        vals = self._tbl.column(value).to_pylist()
        mins: dict = {}
        for k, v in zip(keys, vals):
            if v is None:
                continue
            if k not in mins or v < mins[k]:
                mins[k] = v
        out = [mins.get(k) for k in keys]
        return ArrowFrame(
            self._tbl.append_column(name, _pa_array_typed(out, self._tbl.column(value).type))
        )

    def with_pair_canonical(self, a: str, b: str) -> ArrowFrame:
        import pyarrow as pa
        import pyarrow.compute as pc

        ca, cb = self._tbl.column(a), self._tbl.column(b)
        # An all-null column is null()-typed and breaks element_wise (the W1
        # count_distinct lesson) -- cast it to the partner's type first.
        if pa.types.is_null(ca.type) and not pa.types.is_null(cb.type):
            ca = ca.cast(cb.type)
        elif pa.types.is_null(cb.type) and not pa.types.is_null(ca.type):
            cb = cb.cast(ca.type)
        # element_wise min/max skip nulls by default (polars horizontal parity).
        mn, mx = pc.min_element_wise(ca, cb), pc.max_element_wise(ca, cb)
        tbl = self._tbl.set_column(self._tbl.column_names.index(a), a, mn)
        return ArrowFrame(tbl.set_column(tbl.column_names.index(b), b, mx))

    def unique_by(self, subset: Sequence[str], keep: str = "first") -> ArrowFrame:
        if keep not in ("first", "last"):
            raise ValueError(f"unique_by keep must be first/last, got {keep!r}")
        cols = [self._tbl.column(c).to_pylist() for c in subset]
        kept: dict = {}
        for i, key in enumerate(zip(*cols)):
            if keep == "first":
                kept.setdefault(key, i)
            else:
                kept[key] = i
        return ArrowFrame(self._tbl.take(sorted(kept.values())))

    def with_mod_column(self, src: str, n: int, name: str) -> ArrowFrame:
        import pyarrow as pa

        # No pc.mod kernel; Python % == polars % (PROBED: -11 % 3 == 1 on both).
        vals = self._tbl.column(src).to_pylist()
        out = [None if v is None else v % n for v in vals]
        return ArrowFrame(
            self._tbl.append_column(name, pa.array(out, type=self._tbl.column(src).type))
        )

    def group_min(self, key: str, value: str) -> ArrowFrame:
        import pyarrow as pa

        keys = self._tbl.column(key).to_pylist()
        vals = self._tbl.column(value).to_pylist()
        mins: dict = {}
        order: list = []
        for k, v in zip(keys, vals):
            if k not in mins:
                mins[k] = v
                order.append(k)
            elif v is not None and (mins[k] is None or v < mins[k]):
                mins[k] = v
        return ArrowFrame(
            pa.table(
                {
                    key: pa.array(order, type=self._tbl.column(key).type),
                    value: pa.array(
                        [mins[k] for k in order], type=self._tbl.column(value).type
                    ),
                }
            )
        )

    def select_cast(self, spec: Sequence[tuple[str, str | None, str]]) -> ArrowFrame:
        import pyarrow as pa

        cols = {}
        for src, dtype, alias in spec:
            arr = self._tbl.column(src).combine_chunks()
            if dtype is not None:
                arr = arr.cast(_arrow_dtype(dtype))
            cols[alias] = arr
        return ArrowFrame(pa.table(cols))

    def with_gt_column(self, src: str, threshold: Any, name: str) -> ArrowFrame:
        import pyarrow.compute as pc

        return ArrowFrame(
            self._tbl.append_column(name, pc.greater(self._tbl.column(src), threshold))
        )


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
_SCHEMA_DTYPES = ("int64", "uint32", "float64", "utf8", "bool", "datetime_us")


def _polars_dtype(name: str) -> Any:
    return {
        "int64": pl.Int64,
        "uint32": pl.UInt32,
        "float64": pl.Float64,
        "utf8": pl.Utf8,
        "bool": pl.Boolean,
        # W4a: identity graph frames (resolve.py) use bare pl.Datetime == us, no tz.
        "datetime_us": pl.Datetime,
    }[name]


def _arrow_dtype(name: str) -> Any:
    import pyarrow as pa

    return {
        "int64": pa.int64(),
        "uint32": pa.uint32(),
        "float64": pa.float64(),
        "utf8": pa.large_string(),  # Polars exports Utf8 as LargeUtf8
        "bool": pa.bool_(),
        "datetime_us": pa.timestamp("us"),
    }[name]


def _check_schema(schema: dict[str, str]) -> None:
    bad = [f"{k}={v}" for k, v in schema.items() if v not in _SCHEMA_DTYPES]
    if bad:
        raise ValueError(f"unsupported schema dtype(s) {bad}; supported: {_SCHEMA_DTYPES}")


def _pa_array_typed(values: list, typ: Any) -> Any:
    """pa.array with the source column type when castable, inference fallback."""
    import pyarrow as pa

    try:
        return pa.array(values, type=typ)
    except (pa.ArrowInvalid, pa.ArrowTypeError):  # pragma: no cover -- defensive
        return pa.array(values)


def concat_frames(frames: Sequence[Frame], relaxed: bool = False) -> Frame:
    """Vertical concat, schema-checked by the underlying engine. All frames
    must share a backend (mixing indicates a caller bug, not a coercion
    opportunity). W4a: ``relaxed=True`` == polars vertical_relaxed == arrow
    promote_options="permissive" (PROBED: int64+float64 -> float64 on both)."""
    if not frames:
        raise ValueError("concat_frames requires at least one frame")
    if all(isinstance(f, PolarsFrame) for f in frames):
        how = "vertical_relaxed" if relaxed else "vertical"
        return PolarsFrame(pl.concat([f.native for f in frames], how=how))  # type: ignore[arg-type]
    if all(isinstance(f, ArrowFrame) for f in frames):
        import pyarrow as pa

        if relaxed:
            return ArrowFrame(
                pa.concat_tables([f.native for f in frames], promote_options="permissive")
            )
        return ArrowFrame(pa.concat_tables([f.native for f in frames]))
    raise TypeError("concat_frames requires all frames on the same backend")


def frame_from_column_data(data: dict[str, list], backend: str | None = None) -> Frame:
    """Build a Frame from name -> value-list dicts with ENGINE INFERENCE --
    the db-connector cursor shape (``pl.DataFrame({col: [vals]})``). Values
    parity contract, per-backend dtype inference (all-null columns infer
    Null/null() -- the connector Null->Utf8 promotion recipe stays a native
    caller concern until W5). Empty dict -> zero-column frame."""
    b = backend if backend is not None else resolve_frame_backend()
    if b == "polars":
        return PolarsFrame(pl.DataFrame(data))
    import pyarrow as pa

    return ArrowFrame(pa.table({k: pa.array(v) for k, v in data.items()}))


def frame_from_records(rows: Sequence[dict], backend: str | None = None) -> Frame:
    """Build a Frame from a list of dict rows with ENGINE INFERENCE -- the
    db-connector / a2a / tui boundary shape (``pl.DataFrame(rows)``). Unlike
    ``frame_from_rows`` there is no explicit schema; per-backend dtype
    inference is the documented contract (values parity, not dtype parity).
    Empty rows -> zero-column empty frame on both backends."""
    b = backend if backend is not None else resolve_frame_backend()
    rows = list(rows)
    if b == "polars":
        return PolarsFrame(pl.DataFrame(rows))
    import pyarrow as pa

    if not rows:
        return ArrowFrame(pa.table({}))
    return ArrowFrame(pa.Table.from_pylist(rows))


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


# -- W2c: shared spine schemas + row/column constructors ----------------------

# Backend-neutral schema specs (string dtype vocabulary). Single source of
# truth: scorer's Polars PAIR_STREAM_SCHEMA derives from the pair-stream spec.
PAIR_STREAM_SCHEMA_SPEC: dict[str, str] = {"id_a": "int64", "id_b": "int64", "score": "float64"}
CLUSTER_METADATA_SCHEMA_SPEC: dict[str, str] = {
    "cluster_id": "int64",
    "size": "int64",
    "confidence": "float64",
    "quality": "utf8",
    "oversized": "bool",
    "bottleneck_pair_a": "int64",
    "bottleneck_pair_b": "int64",
    "min_edge": "float64",
    "avg_edge": "float64",
}


def frame_from_rows(
    rows: Sequence[Any], schema: dict[str, str], backend: str | None = None
) -> Frame:
    """Build a Frame from ROW-oriented data -- tuple rows (scorer's pair
    lists) or dict rows (cluster's split-metadata rows) -- with an EXPLICIT
    string schema (no inference; where a raw call site infers, the port
    asserts the explicit dtypes instead)."""
    _check_schema(schema)
    names = list(schema)
    if rows and isinstance(rows[0], dict):
        data = {n: [r[n] for r in rows] for n in names}
    else:
        data = {n: [r[i] for r in rows] for i, n in enumerate(names)}
    return frame_from_columns(data, schema, backend=backend)


def concat_columns(cols: Sequence[Column]) -> Column:
    """Vertical concat of Columns (cluster.py's all-ids build); backends must
    match. `.unique()` composes on the result."""
    if not cols:
        raise ValueError("concat_columns requires at least one column")
    if all(isinstance(c, PolarsColumn) for c in cols):
        return PolarsColumn(pl.concat([c._s for c in cols]))  # noqa: SLF001
    if all(isinstance(c, ArrowColumn) for c in cols):
        import pyarrow as pa

        chunks: list[Any] = []
        for c in cols:
            arr = c.to_arrow()
            chunks.extend(arr.chunks if isinstance(arr, pa.ChunkedArray) else [arr])
        return ArrowColumn(
            pa.concat_arrays(
                [ch.combine_chunks() if isinstance(ch, pa.ChunkedArray) else ch for ch in chunks]
            )
        )
    raise TypeError("concat_columns requires all columns on the same backend")
