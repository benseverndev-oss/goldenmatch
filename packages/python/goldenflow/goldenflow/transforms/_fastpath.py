"""Vectorized-fast-path-with-residual-fallback helper.

The expensive GoldenFlow transforms (`date_iso8601`, `phone_e164`, ...) call a
Python library (`dateutil`, `phonenumbers`) once per row via
``Series.map_elements``. On a realistic 1M-row frame that is ~0.04-0.06 M
rows/s — interpreter-bound, and 90%+ of the whole pipeline wall (measured: the
two date/phone transforms are ~44 s of a ~48 s 1M-row run).

The fix that needs no native code: run a *vectorized* Polars expression that
resolves the well-formed common case in Rust, and fall back to the per-row
Python function ONLY for the residual rows it couldn't resolve. On clean data
the residual is empty, so the slow path never runs (117x on dates, 17x on US
phones in measurement); on ragged data the cost is proportional to the ragged
tail, never the whole column.

**Parity contract.** ``apply_with_residual`` returns a result that is identical
to applying ``slow_fn`` to every row, *provided* ``fast_expr`` agrees with
``slow_fn`` on every row it resolves to a non-null value. The fast path must
therefore only resolve rows it is certain about and leave everything else null
(the residual). Each caller's parity test asserts this equivalence against the
pure ``map_elements`` reference over a random corpus. This is the same
"accelerate the common case, defer to the reference for the tail" shape that
``date_iso8601`` already used for year-only columns, generalized.
"""
from __future__ import annotations

from collections.abc import Callable

import polars as pl

_V = "__gf_v__"
_I = "__gf_i__"
_FAST = "__gf_fast__"
_SLOW = "__gf_slow__"


def apply_with_residual(
    series: pl.Series,
    fast_expr: pl.Expr,
    slow_fn: Callable[[str | None], object],
    return_dtype: pl.DataType,
) -> pl.Series:
    """Resolve ``series`` with a vectorized ``fast_expr`` (computed over a column
    named :data:`_V`), filling any row the fast path leaves null — but whose
    input was non-null — by calling ``slow_fn`` per row.

    Output column name and length match ``series``. Null inputs stay null.
    """
    name = series.name
    df = pl.DataFrame({_V: series}).with_row_index(_I)
    df = df.with_columns(fast_expr.alias(_FAST))

    residual = df.filter(pl.col(_FAST).is_null() & pl.col(_V).is_not_null())
    if residual.height == 0:
        return df.get_column(_FAST).rename(name)

    fixed = residual.select(
        pl.col(_I),
        pl.col(_V).map_elements(slow_fn, return_dtype=return_dtype).alias(_SLOW),
    )
    merged = (
        df.join(fixed, on=_I, how="left")
        .sort(_I)  # left join does not guarantee row order; restore it
        .select(pl.coalesce([pl.col(_FAST), pl.col(_SLOW)]).alias(name))
    )
    return merged.get_column(name)
