"""Polars-free derivation of transformed block-key / score columns (W2a).

pyarrow twins of the Polars expression derivation the fused prep uses today
(`blocker._build_block_key_expr` / `scorer._get_transformed_values`), pinned
value-for-value equal by ``tests/test_arrow_derive_parity.py``. The Polars
implementations remain the reference until W5; every divergence found by the
parity fixtures is fixed HERE, never papered over downstream.

Parity contract (each item is a named fixture):

- Cast targets ``pa.large_string()`` (Polars exports LargeUtf8).
- Float64 stringification matches Polars' Rust formatter, which ``pc.cast``
  does NOT (``1.0`` -> ``"1.0"`` vs ``"1"``, ``NaN`` vs ``"nan"``,
  ``-0.0`` vs ``"-0"``): floats go through a Python-side formatter built on
  ``repr`` plus the empirically-probed deltas (decimal rendering at the 1e-5
  band, unpadded negative exponents).
- ``\\s`` differs between engines (Rust regex = Unicode White_Space, RE2 =
  ASCII): the whitespace transforms use an explicit White_Space class.
- Composite keys null-propagate like ``pl.concat_str(ignore_nulls=False)``
  (any null field -> null key); single-field keys skip the join entirely.
- The non-native fallback applies ``apply_transforms`` per value on the CAST
  strings with nulls preserved (Polars ``map_elements`` never sees nulls).
- ``address_normalize`` is NEVER native here (even under
  ``GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE=1``): the arrow lane always takes the
  ``apply_transforms`` plugin fallback, sidestepping the known env-gated
  parity edge the Polars chain carries (see matchkey.py's gate note).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from goldenmatch.utils.transforms import apply_transforms

# RE2 has no Unicode-aware \s (Rust regex does). Explicit White_Space class:
# Zs+Zl+Zp via \p{Z}, plus the non-Z members (tab/newline/vtab/formfeed/CR/NEL).
_WS_CLASS = r"[\t\n\x0b\f\r\x{0085}\p{Z}]"


def _fmt_f64(v: float) -> str:
    """Polars-parity float64 -> str (probed 2026-07-10; fixtures re-verify).

    ``repr`` matches Polars except: NaN capitalization, the 1e-5 band (Polars
    renders decimally, Python scientifically), and zero-padded negative
    exponents (Python ``1e-06`` vs Polars ``1e-6``).
    """
    if math.isnan(v):
        return "NaN"
    r = repr(v)
    if "e" not in r:
        return r
    mant, _, exp = r.partition("e")
    iexp = int(exp)
    if iexp >= 0:
        return r  # "1e+16" style matches Polars exactly
    if iexp == -5:
        sign = "-" if mant.startswith("-") else ""
        digits = mant.lstrip("-").replace(".", "")
        return f"{sign}0.0000{digits}"
    return f"{mant}e-{-iexp}"


def _fmt_f32_array(arr: Any) -> list[str | None]:
    """float32 needs the f32 shortest-repr (to_pylist widens to f64 and
    ``repr`` would print representation noise). numpy's f32 str is the
    shortest repr; the exponent deltas are normalized like ``_fmt_f64``."""
    import numpy as np

    out: list[str | None] = []
    np_vals = arr.to_numpy(zero_copy_only=False)  # nulls -> nan; masked via to_pylist
    nulls = [v is None for v in arr.to_pylist()]
    for i, v in enumerate(np_vals):
        if nulls[i]:
            out.append(None)
            continue
        f = np.float32(v)
        if np.isnan(f):
            out.append("NaN")
            continue
        r = str(f)
        if "e" in r:
            mant, _, exp = r.partition("e")
            iexp = int(exp)
            r = r if iexp >= 0 else f"{mant}e-{-iexp}"
        out.append(r)
    return out


def cast_utf8(arr: Any) -> Any:
    """``pl.cast(pl.Utf8)`` twin: any column -> ``pa.large_string()`` array.

    Nulls stay null for every dtype. ChunkedArrays are combined; dictionary
    arrays are decoded first (Polars round-trips them through Categorical).
    """
    import pyarrow as pa
    import pyarrow.compute as pc

    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    if pa.types.is_dictionary(arr.type):
        arr = arr.dictionary_decode()
    if pa.types.is_null(arr.type):
        return pa.nulls(len(arr), pa.large_string())
    if pa.types.is_large_string(arr.type):
        return arr
    if pa.types.is_string(arr.type):
        return pc.cast(arr, pa.large_string())
    if pa.types.is_float64(arr.type) or pa.types.is_float16(arr.type):
        vals = [None if v is None else _fmt_f64(float(v)) for v in arr.to_pylist()]
        return pa.array(vals, type=pa.large_string())
    if pa.types.is_float32(arr.type):
        return pa.array(_fmt_f32_array(arr), type=pa.large_string())
    return pc.cast(arr, pa.large_string())


def _native_chain(arr: Any, transforms: Sequence[str]) -> Any | None:
    """pc twin of ``matchkey._try_native_chain`` over an ALREADY-CAST
    large_string array. None if any transform needs the Python fallback
    (soundex/metaphone/qgram/... and, deliberately, address_normalize)."""
    import pyarrow.compute as pc

    for t in transforms:
        if t == "lowercase":
            arr = pc.utf8_lower(arr)
        elif t == "uppercase":
            arr = pc.utf8_upper(arr)
        elif t == "strip":
            arr = pc.utf8_trim_whitespace(arr)
        elif t.startswith("substring:"):
            parts = t.split(":")
            start, stop = int(parts[1]), int(parts[2])
            arr = pc.utf8_slice_codeunits(arr, start=start, stop=stop)
        elif t == "normalize_whitespace":
            arr = pc.utf8_trim_whitespace(
                pc.replace_substring_regex(arr, pattern=f"{_WS_CLASS}+", replacement=" ")
            )
        elif t == "strip_all":
            arr = pc.replace_substring_regex(arr, pattern=f"{_WS_CLASS}+", replacement="")
        elif t == "digits_only":
            arr = pc.replace_substring_regex(arr, pattern="[^0-9]", replacement="")
        elif t == "alpha_only":
            arr = pc.replace_substring_regex(arr, pattern="[^a-zA-Z]", replacement="")
        else:
            return None
    return arr


def transformed_column(arr: Any, transforms: Sequence[str]) -> Any:
    """One field's cast-then-chain, mirroring the fused prep's per-field
    derivation: Utf8 cast, then the transform chain (native pc where the whole
    chain is expressible, else per-value ``apply_transforms`` with nulls
    preserved). Returns a ``pa.large_string()`` array."""
    import pyarrow as pa

    cast = cast_utf8(arr)
    if not transforms:
        return cast
    native = _native_chain(cast, list(transforms))
    if native is not None:
        return native
    vals = [
        apply_transforms(v, list(transforms)) if v is not None else None for v in cast.to_pylist()
    ]
    return pa.array(vals, type=pa.large_string())


def block_key(field_arrs: Sequence[Any], transforms: Sequence[str], sep: str = "||") -> Any:
    """``blocker._build_block_key_expr`` twin: the SAME transform chain applies
    to every key field, then fields concat with ``sep`` (any-null -> null,
    matching ``pl.concat_str``); a single field skips the join entirely."""
    import pyarrow as pa
    import pyarrow.compute as pc

    cols = [transformed_column(a, transforms) for a in field_arrs]
    if len(cols) == 1:
        return cols[0]
    sep_scalar = pa.scalar(sep, type=pa.large_string())  # match the column type
    return pc.binary_join_element_wise(*cols, sep_scalar, null_handling="emit_null")
