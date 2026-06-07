"""Vectorized-fast-path-with-residual-fallback helper.

The expensive GoldenFlow transforms (`date_iso8601`, `phone_e164`, ...) call a
Python library (`dateutil`, `phonenumbers`) once per row via
``Series.map_elements``. On a realistic 1M-row frame that is ~0.04-0.06 M
rows/s — interpreter-bound, and 90%+ of the whole pipeline wall (measured: the
two date/phone transforms are ~44 s of a ~48 s 1M-row run).

Resolution proceeds in up to three tiers, cheapest first:

1. **Vectorized fast path** (``fast_expr``) — a Polars expression that resolves
   the well-formed common case in Rust, leaving everything it isn't certain
   about null.
2. **Native kernel** (optional ``native_fn``) — the ``goldenflow-native`` Rust
   kernel, run on just the residual rows. Off unless the component cleared the
   parity gate (see ``goldenflow.core._native_loader``).
3. **Per-row reference** (``slow_fn``) — the original ``dateutil`` /
   ``phonenumbers`` path, run on whatever the first two tiers left null.

**Parity contract.** The result is identical to applying ``slow_fn`` to every
row, *provided* every tier agrees with ``slow_fn`` on the rows it resolves to a
non-null value. The fast path therefore only resolves rows it is certain about;
the native tier is only enabled for components whose parity has been signed off.
On clean data the residual is empty, so tiers 2-3 never run (117x on dates, 17x
on US phones in measurement); on ragged data the cost is proportional to the
ragged tail, never the whole column.
"""
from __future__ import annotations

from collections.abc import Callable

import polars as pl

_V = "__gf_v__"
_I = "__gf_i__"
_FAST = "__gf_fast__"
_NAT = "__gf_nat__"
_PY = "__gf_py__"
_SLOW = "__gf_slow__"


def apply_with_residual(
    series: pl.Series,
    fast_expr: pl.Expr,
    slow_fn: Callable[[str | None], object],
    return_dtype: pl.DataType,
    native_fn: Callable[[pl.Series], pl.Series] | None = None,
) -> pl.Series:
    """Resolve ``series`` with a vectorized ``fast_expr`` (computed over a column
    named :data:`_V`), then — for rows it leaves null whose input was non-null —
    an optional ``native_fn`` batch kernel, then the per-row ``slow_fn``.

    Output column name and length match ``series``. Null inputs stay null.
    """
    name = series.name
    df = pl.DataFrame({_V: series}).with_row_index(_I)
    df = df.with_columns(fast_expr.alias(_FAST))

    residual = df.filter(pl.col(_FAST).is_null() & pl.col(_V).is_not_null())
    if residual.height == 0:
        return df.get_column(_FAST).rename(name)

    # Tier 2: native kernel on the residual (batch), if provided.
    if native_fn is not None:
        nat = native_fn(residual.get_column(_V)).rename(_NAT)
        residual = residual.with_columns(nat)
    else:
        residual = residual.with_columns(pl.lit(None, dtype=return_dtype).alias(_NAT))

    # Tier 3: per-row reference for whatever native left null.
    need_py = residual.filter(pl.col(_NAT).is_null())
    if need_py.height:
        py_fixed = need_py.select(
            pl.col(_I),
            pl.col(_V).map_elements(slow_fn, return_dtype=return_dtype).alias(_PY),
        )
        residual = residual.join(py_fixed, on=_I, how="left").with_columns(
            pl.coalesce([pl.col(_NAT), pl.col(_PY)]).alias(_SLOW)
        )
    else:
        residual = residual.with_columns(pl.col(_NAT).alias(_SLOW))

    fixed = residual.select(pl.col(_I), pl.col(_SLOW))
    merged = (
        df.join(fixed, on=_I, how="left")
        .sort(_I)  # left join does not guarantee row order; restore it
        .select(pl.coalesce([pl.col(_FAST), pl.col(_SLOW)]).alias(name))
    )
    return merged.get_column(name)
