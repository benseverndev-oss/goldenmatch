"""Batch record fingerprinting for identity id derivation.

``batch_fingerprints(df)`` returns one fingerprint per row of a records
DataFrame -- a byte-identical drop-in for the per-row list comprehension
``[record_fingerprint(_canonical_payload(payload)) for r in df.to_dicts()]``
(with ``None`` for un-fingerprintable rows -> caller uses the legacy id).

Entity ids key off these hashes, so byte-identical parity with the per-row
path is the durability invariant. Fully-batchable rows go through the Arrow
batch kernel after a vectorized canonicalization; anything that can't reach
byte-identical parity via a Polars cast routes to the per-row fallback
(column-level -- the whole frame -- or row-level -- only the offending rows).
"""
from __future__ import annotations

import math
from typing import Any

from goldenmatch._polars_lazy import pl


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a row payload to the primitives ``record_fingerprint`` accepts.

    Temporals -> ISO-8601; non-finite floats -> their token string; other
    non-primitives -> ``str()``. v1 of the canonical spec is primitive-only;
    these coercions are a documented v1.1 follow-up (pinned in the kernel +
    mirrored across surfaces when a second write surface adopts the C ABI). For
    now they keep ingest working on real data (date columns etc.) while clean
    str/int/float/bool/None records get a fully cross-surface fingerprint."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, float) and not math.isfinite(v):
            out[k] = repr(v)  # "nan" / "inf" / "-inf" as a stable token
        elif v is None or isinstance(v, (bool, int, float, str, bytes)):
            out[k] = v
        else:
            iso = getattr(v, "isoformat", None)
            out[k] = iso() if callable(iso) else str(v)
    return out


_INT_UPCAST_CACHE: tuple[type, ...] | None = None


def _int_upcast_dtypes() -> tuple[type, ...]:
    """Integer dtypes that up-cast to Int64 without changing the canonical
    value. Built lazily (not a module-level ``pl.`` tuple literal) so
    importing this module doesn't import Polars eagerly (W0 polars-eviction
    gate); cached after the first call."""
    global _INT_UPCAST_CACHE
    if _INT_UPCAST_CACHE is None:
        _INT_UPCAST_CACHE = (
            pl.Int8, pl.Int16, pl.Int32,
            pl.UInt8, pl.UInt16, pl.UInt32,
        )
    return _INT_UPCAST_CACHE


_I64_MAX = 2**63 - 1


def _is_unbatchable_dtype(dtype: pl.DataType) -> bool:
    """True for a column-level un-batchable dtype: no parity-preserving cast
    exists, so the WHOLE frame must go per-row (the column is in every row's
    hash). ``Date`` and ``us``-unit tz-naive ``Datetime`` are batchable via the
    temporal recipe and are NOT flagged here."""
    if dtype == pl.Binary:
        return True
    if dtype == pl.Duration or dtype == pl.Time:
        return True
    if dtype == pl.Decimal:
        return True
    if isinstance(dtype, pl.Datetime):
        # tz-aware or non-us unit cannot be reproduced as Python isoformat()
        # by the temporal recipe below.
        if dtype.time_zone is not None or dtype.time_unit != "us":
            return True
        return False
    if isinstance(dtype, (pl.List, pl.Array, pl.Struct, pl.Object)):
        return True
    return False


def canonicalize_records_df(df):
    """Return ``(batch_df_or_None, fallback_mask)``.

    A5 NOTE: accepts a pa.Table via an entry bridge -- the dtype-driven
    canonicalization below implements the cross-surface :h1: fingerprint
    contract; its arrow port is a dedicated batch (A5b in the endgame
    plan) with the fingerprint parity corpus as the gate.

    ``batch_df`` is a canonicalized frame (clean Int64/finite-Float64/Bool/Utf8
    /typed-Null) restricted to the fully-batchable rows, in ORIGINAL row order,
    ready for ``record_fingerprints_batch_arrow``. ``fallback_mask[i]`` is True
    when row ``i`` must go through the per-row path instead.

    ``batch_df`` is ``None`` (and the mask all-True) when any column-level
    un-batchable dtype is present -- that column is in every row's hash, so no
    row can be batched.
    """
    if not isinstance(df, pl.DataFrame):  # A5b entry bridge (see docstring)
        df = pl.from_arrow(df)
    height = df.height
    # Drop __-prefixed columns (both kernels + per-row payload exclude them).
    keep_cols = [c for c in df.columns if not c.startswith("__")]
    if not keep_cols:
        # No content fields after dropping __-cols: route the whole frame to per-row
        # (each row hashes the empty payload {} -> a fixed hash). Cheap + parity-safe.
        return None, [True] * height
    sub = df.select(keep_cols)

    # Column-level un-batchable -> whole frame per-row.
    for col in keep_cols:
        if _is_unbatchable_dtype(sub.schema[col]):
            return None, [True] * height

    # Build the union row-level fallback mask BEFORE any cast. Two row-level
    # cases: non-finite Float64 cells, and UInt64 cells > Int64 max.
    mask = pl.Series([False] * height, dtype=pl.Boolean)
    for col in keep_cols:
        dtype = sub.schema[col]
        if dtype == pl.Float64:
            non_finite = ~sub[col].is_finite() & sub[col].is_not_null()
            mask = mask | non_finite.fill_null(False)
        elif dtype == pl.Float32:
            non_finite = ~sub[col].cast(pl.Float64).is_finite() & sub[col].is_not_null()
            mask = mask | non_finite.fill_null(False)
        elif dtype == pl.UInt64:
            overflow = (sub[col] > _I64_MAX).fill_null(False)
            mask = mask | overflow

    mask_list = mask.to_list()

    # Restrict to surviving rows (original order preserved by filter), THEN
    # cast -- so an overflow UInt64 cell never reaches a column-wide Int64 cast
    # (which would raise InvalidOperationError, not TypeError/ValueError).
    survivors = sub.filter(~mask)

    exprs: list[pl.Expr] = []
    for col in keep_cols:
        dtype = survivors.schema[col]
        if dtype == pl.Date:
            exprs.append(pl.col(col).dt.to_string("%Y-%m-%d"))
        elif isinstance(dtype, pl.Datetime):
            # us-unit tz-naive (others were filtered as un-batchable above).
            exprs.append(
                pl.col(col)
                .dt.to_string("%Y-%m-%dT%H:%M:%S%.6f")
                .str.replace(r"\.000000$", "")
            )
        elif dtype == pl.Float32:
            exprs.append(pl.col(col).cast(pl.Float64))
        elif isinstance(dtype, _int_upcast_dtypes()) or dtype == pl.UInt64:
            # UInt64 handled here not in _int_upcast_dtypes(): its overflow rows are already masked out above
            exprs.append(pl.col(col).cast(pl.Int64))
        elif isinstance(dtype, (pl.Categorical, pl.Enum)):
            exprs.append(pl.col(col).cast(pl.Utf8))
        elif dtype == pl.Null:
            # Untyped all-null column: kernel rejects the Arrow null type; Utf8
            # null cells hash as FpValue::Null, matching the per-row path.
            exprs.append(pl.col(col).cast(pl.Utf8))
        else:
            exprs.append(pl.col(col))

    batch_df = survivors.with_columns(exprs) if exprs else survivors
    return batch_df, mask_list


def batch_fingerprints(df) -> list[str | None]:
    """One fingerprint per row of ``df``, in row order. ``None`` for a row the
    canonical spec can't fingerprint (caller falls back to the legacy id).

    Byte-identical to ``[record_fingerprint(_canonical_payload(payload))
    for r in df.to_dicts()]`` (with ``__``-prefixed keys dropped and
    ``TypeError``/``ValueError`` mapped to ``None``)."""
    from goldenmatch.core._hashing import (
        record_fingerprint,
        record_fingerprints_batch_arrow,
    )

    if not isinstance(df, pl.DataFrame):  # A5b entry bridge (see canonicalize)
        df = pl.from_arrow(df)
    out: list[str | None] = [None] * df.height
    batch_df, mask = canonicalize_records_df(df)
    if batch_df is not None and batch_df.height:
        hashes = record_fingerprints_batch_arrow(batch_df)  # aligned to batch rows
        # batch_df.height == count(not m for m in mask), guaranteed by survivors = sub.filter(~mask)
        bi = 0
        for i in range(df.height):
            if not mask[i]:
                out[i] = hashes[bi]
                bi += 1
    rows = df.to_dicts()
    for i in range(df.height):
        if mask[i]:
            payload = {k: v for k, v in rows[i].items() if not k.startswith("__")}
            try:
                out[i] = record_fingerprint(_canonical_payload(payload))
            except (TypeError, ValueError):
                out[i] = None
    return out


# -- W4b-2: pyarrow twin ------------------------------------------------------
# The arrow-lane canonicalizer: same routing contract as
# canonicalize_records_df, dispatched on the pyarrow dtype lattice. PROBED
# parity recipes (2026-07-11): date32.cast(string) == "%Y-%m-%d";
# pc.strftime(us-timestamp, "%Y-%m-%dT%H:%M:%S") ALWAYS appends 6-digit
# subseconds -> trimming "\.000000$" reproduces Python isoformat()/the
# polars recipe byte-for-byte.


def _is_unbatchable_arrow_type(typ) -> bool:
    import pyarrow as pa

    if pa.types.is_binary(typ) or pa.types.is_large_binary(typ):
        return True
    if pa.types.is_duration(typ) or pa.types.is_time(typ):
        return True
    if pa.types.is_decimal(typ):
        return True
    if pa.types.is_timestamp(typ):
        return typ.tz is not None or typ.unit != "us"
    if (
        pa.types.is_list(typ)
        or pa.types.is_large_list(typ)
        or pa.types.is_fixed_size_list(typ)
        or pa.types.is_struct(typ)
        or pa.types.is_map(typ)
    ):
        return True
    return False


def canonicalize_records_table(tbl):
    """pa.Table twin of ``canonicalize_records_df`` -- returns
    ``(batch_tbl_or_None, fallback_mask)`` with the identical routing
    contract (cross-pinned by test_fingerprint_batch_arrow_twin)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    height = tbl.num_rows
    keep_cols = [c for c in tbl.column_names if not c.startswith("__")]
    if not keep_cols:
        return None, [True] * height
    sub = tbl.select(keep_cols)

    for col in keep_cols:
        if _is_unbatchable_arrow_type(sub.schema.field(col).type):
            return None, [True] * height

    mask = pa.array([False] * height, type=pa.bool_())
    for col in keep_cols:
        typ = sub.schema.field(col).type
        arr = sub.column(col)
        if pa.types.is_float64(typ) or pa.types.is_float32(typ):
            f64 = arr.cast(pa.float64()) if pa.types.is_float32(typ) else arr
            non_finite = pc.and_kleene(
                pc.invert(pc.is_finite(f64)), pc.is_valid(f64)
            )
            mask = pc.or_(mask, pc.fill_null(non_finite, False))
        elif pa.types.is_uint64(typ):
            # uint64-typed scalar: the comparison kernel would otherwise promote
            # the ARRAY to int64 and raise on the very overflow values we mask.
            overflow = pc.fill_null(
                pc.greater(arr, pa.scalar(_I64_MAX, type=pa.uint64())), False
            )
            mask = pc.or_(mask, overflow)

    mask_list = mask.to_pylist()
    survivors = sub.filter(pc.invert(mask))

    out_cols = []
    for col in keep_cols:
        typ = survivors.schema.field(col).type
        arr = survivors.column(col).combine_chunks()
        if pa.types.is_date(typ):
            out_cols.append(arr.cast(pa.large_string()))
        elif pa.types.is_timestamp(typ):
            # us-unit tz-naive (others routed un-batchable above).
            iso = pc.strftime(arr, format="%Y-%m-%dT%H:%M:%S")
            out_cols.append(
                pc.replace_substring_regex(
                    iso.cast(pa.large_string()), pattern=r"\.000000$", replacement=""
                )
            )
        elif pa.types.is_float32(typ):
            out_cols.append(arr.cast(pa.float64()))
        elif pa.types.is_integer(typ) and not pa.types.is_int64(typ):
            out_cols.append(arr.cast(pa.int64()))
        elif pa.types.is_dictionary(typ):
            out_cols.append(arr.cast(pa.large_string()))
        elif pa.types.is_null(typ):
            # Kernel rejects the Arrow null type; large_string nulls hash as
            # FpValue::Null, matching the per-row path.
            out_cols.append(arr.cast(pa.large_string()))
        else:
            out_cols.append(arr)

    batch_tbl = pa.table(dict(zip(keep_cols, out_cols)))
    return batch_tbl, mask_list


def batch_fingerprints_table(tbl) -> list[str | None]:
    """pa.Table twin of ``batch_fingerprints`` (arrow lane). Byte-identical
    output values by the same canonical spec."""
    from goldenmatch.core._hashing import (
        record_fingerprint,
        record_fingerprints_batch_arrow,
    )

    height = tbl.num_rows
    out: list[str | None] = [None] * height
    batch_tbl, mask = canonicalize_records_table(tbl)
    if batch_tbl is not None and batch_tbl.num_rows:
        hashes = record_fingerprints_batch_arrow(batch_tbl)
        bi = 0
        for i in range(height):
            if not mask[i]:
                out[i] = hashes[bi]
                bi += 1
    rows = tbl.to_pylist()
    for i in range(height):
        if mask[i]:
            payload = {k: v for k, v in rows[i].items() if not k.startswith("__")}
            try:
                out[i] = record_fingerprint(_canonical_payload(payload))
            except (TypeError, ValueError):
                out[i] = None
    return out
