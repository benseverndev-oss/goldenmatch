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

import polars as pl


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


# Integer dtypes that up-cast to Int64 without changing the canonical value.
_INT_UPCAST = (
    pl.Int8, pl.Int16, pl.Int32,
    pl.UInt8, pl.UInt16, pl.UInt32,
)
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


def canonicalize_records_df(df: pl.DataFrame):
    """Return ``(batch_df_or_None, fallback_mask)``.

    ``batch_df`` is a canonicalized frame (clean Int64/finite-Float64/Bool/Utf8
    /typed-Null) restricted to the fully-batchable rows, in ORIGINAL row order,
    ready for ``record_fingerprints_batch_arrow``. ``fallback_mask[i]`` is True
    when row ``i`` must go through the per-row path instead.

    ``batch_df`` is ``None`` (and the mask all-True) when any column-level
    un-batchable dtype is present -- that column is in every row's hash, so no
    row can be batched.
    """
    height = df.height
    # Drop __-prefixed columns (both kernels + per-row payload exclude them).
    keep_cols = [c for c in df.columns if not c.startswith("__")]
    sub = df.select(keep_cols) if keep_cols else df.select([])

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
        elif isinstance(dtype, _INT_UPCAST) or dtype == pl.UInt64:
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


def batch_fingerprints(df: pl.DataFrame) -> list[str | None]:
    """One fingerprint per row of ``df``, in row order. ``None`` for a row the
    canonical spec can't fingerprint (caller falls back to the legacy id).

    Byte-identical to ``[record_fingerprint(_canonical_payload(payload))
    for r in df.to_dicts()]`` (with ``__``-prefixed keys dropped and
    ``TypeError``/``ValueError`` mapped to ``None``)."""
    from goldenmatch.core._hashing import (
        record_fingerprint,
        record_fingerprints_batch_arrow,
    )

    out: list[str | None] = [None] * df.height
    batch_df, mask = canonicalize_records_df(df)
    if batch_df is not None and batch_df.height:
        hashes = record_fingerprints_batch_arrow(batch_df)  # aligned to batch rows
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
