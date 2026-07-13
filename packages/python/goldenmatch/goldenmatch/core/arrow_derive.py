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
    # PERF (hotspot round 2): a SINGLE python-fallback transform (soundex /
    # metaphone are the common case) resolves its callable ONCE instead of
    # re-dispatching apply_transform's name chain per value -- ~0.6s/1M.
    # Byte-identical: apply_transform(v, t) == the resolved fn(v).
    if len(transforms) == 1:
        from functools import partial

        from goldenmatch.utils.transforms import apply_transform

        _t = transforms[0]
        # Known phonetic transforms resolve to their (Rust) callables
        # directly, skipping apply_transform's name-dispatch per value.
        if _t == "soundex":
            import jellyfish

            _fn = jellyfish.soundex
        elif _t == "metaphone":
            import jellyfish

            _fn = jellyfish.metaphone
        else:
            _fn = partial(apply_transform, transform=_t)
        vals = [None if v is None else _fn(v) for v in cast.to_pylist()]
    else:
        chain = list(transforms)
        vals = [
            None if v is None else apply_transforms(v, chain)
            for v in cast.to_pylist()
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


# -- W2e-1: standardizer twins (_NATIVE_STANDARDIZERS' pc equivalents) --------
#
# Each builder takes and returns a pa large_string array (post-cast_utf8).
# Parity contract additions (fixtures in tests/test_arrow_std_parity.py):
# - null-if-empty: empty string -> null; a null input STAYS null (both
#   engines agree: the null condition falls through to the null expr).
# - `\D` is Unicode in Polars (Rust regex \d = \p{Nd}); RE2 needs the
#   explicit `\P{Nd}` class (probed 2026-07-10).
# - pc.utf8_title matched pl.str.to_titlecase on the full hazard corpus
#   (hyphens, apostrophes, digits, underscores, non-ASCII) -- fixture-pinned,
#   name_proper goes native.
# - `address` ALWAYS declines to the pure-Python std_address fallback
#   (split + list.eval + coalesce + dict-replace has no pc analog).


def _std_null_if_empty(arr: Any) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    empty = pc.equal(pc.utf8_length(arr), 0)
    return pc.if_else(pc.fill_null(empty, False), pa.scalar(None, type=pa.large_string()), arr)


def _std_strip(arr: Any) -> Any:
    import pyarrow.compute as pc

    return _std_null_if_empty(pc.utf8_trim_whitespace(arr))


def _std_name_upper(arr: Any) -> Any:
    import pyarrow.compute as pc

    return _std_null_if_empty(pc.utf8_upper(pc.utf8_trim_whitespace(arr)))


def _std_name_lower(arr: Any) -> Any:
    import pyarrow.compute as pc

    return _std_null_if_empty(pc.utf8_lower(pc.utf8_trim_whitespace(arr)))


def _std_name_proper(arr: Any) -> Any:
    import pyarrow.compute as pc

    return _std_null_if_empty(pc.utf8_title(pc.utf8_trim_whitespace(arr)))


def _std_trim_whitespace(arr: Any) -> Any:
    import pyarrow.compute as pc

    e = pc.utf8_trim_whitespace(arr)
    e = pc.replace_substring_regex(e, pattern=f"{_WS_CLASS}+", replacement=" ")
    return _std_null_if_empty(e)


def _std_phone(arr: Any) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    digits = pc.replace_substring_regex(arr, pattern=r"\P{Nd}", replacement="")
    is11 = pc.fill_null(pc.equal(pc.utf8_length(digits), 11), False)
    stripped = pc.if_else(is11, pc.utf8_slice_codeunits(digits, start=1), digits)
    short = pc.fill_null(pc.less(pc.utf8_length(stripped), 7), False)
    return pc.if_else(short, pa.scalar(None, type=pa.large_string()), stripped)


def _std_zip5(arr: Any) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    digits = pc.replace_substring_regex(arr, pattern="[^0-9]", replacement="")
    padded = pc.utf8_lpad(pc.utf8_slice_codeunits(digits, start=0, stop=5), width=5, padding="0")
    empty = pc.fill_null(pc.equal(pc.utf8_length(digits), 0), False)
    return pc.if_else(empty, pa.scalar(None, type=pa.large_string()), padded)


def _std_email(arr: Any) -> Any:
    import pyarrow as pa
    import pyarrow.compute as pc

    cleaned = pc.utf8_lower(pc.utf8_trim_whitespace(arr))
    ok = pc.and_kleene(
        pc.and_kleene(
            pc.match_substring(cleaned, "@"),
            pc.match_substring_regex(cleaned, r"@[^@]+\."),
        ),
        pc.greater(pc.utf8_length(cleaned), 0),
    )
    return pc.if_else(pc.fill_null(ok, False), cleaned, pa.scalar(None, type=pa.large_string()))


_ARROW_STANDARDIZERS: dict[str, Any] = {
    "strip": _std_strip,
    "name_upper": _std_name_upper,
    "name_lower": _std_name_lower,
    "name_proper": _std_name_proper,
    "state": _std_name_upper,  # std_state == strip+upper (same builder shape)
    "trim_whitespace": _std_trim_whitespace,
    "phone": _std_phone,
    "zip5": _std_zip5,
    "email": _std_email,
    # "address": deliberately absent -> python fallback (std_address oracle).
}


def standardized_column(arr: Any, std_names: Sequence[str]) -> Any:
    """One field's standardizer chain over Arrow, mirroring
    ``standardize._try_build_native_chain``'s covered set: cast, then each
    named standardizer via its pc twin; any UNCOVERED name (``address``,
    plugins) sends the whole chain to the per-value pure-Python
    ``STANDARDIZERS`` fallback (the byte-exact oracle), nulls preserved."""
    import pyarrow as pa

    cast = cast_utf8(arr)
    covered = [n for n in std_names if n in _ARROW_STANDARDIZERS]
    uncovered = [n for n in std_names if n not in _ARROW_STANDARDIZERS]
    out = cast
    for n in covered:
        out = _ARROW_STANDARDIZERS[n](out)
    if not uncovered:
        return out
    # Mixed chain mirrors apply_standardization's REORDERING quirk exactly:
    # the Polars path applies every native builder first, then the non-native
    # tail via one chained map_elements UDF (standardize.py:455-476). The
    # std_* fns handle None themselves, so mid-chain nulls flow through.
    from goldenmatch.core.standardize import get_standardizer

    fns = [get_standardizer(n) for n in uncovered]
    vals: list[str | None] = []
    for v in out.to_pylist():
        for fn in fns:
            v = fn(v)
        vals.append(v)
    return pa.array(vals, type=pa.large_string())


# -- W2e-2: matchkey composite derivation --------------------------------------


def matchkey_field_column(arr: Any, transforms: Sequence[str]) -> Any:
    """One matchkey FIELD's derivation, mirroring build_matchkey_expr's
    per-field branches (matchkey.py:164-179) -- which differ from the fused
    prep's cast-then-chain contract in ONE way: the non-native fallback runs
    ``apply_transforms`` over the RAW values (matchkey's map_elements has no
    Utf8 pre-cast), so this twin must too."""
    import pyarrow as pa

    if not transforms:
        return cast_utf8(arr)
    cast = cast_utf8(arr)
    native = _native_chain(cast, list(transforms))
    if native is not None:
        return native
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks()
    vals = [
        apply_transforms(v, list(transforms)) if v is not None else None
        for v in arr.to_pylist()  # RAW values, deliberately un-cast
    ]
    return pa.array(vals, type=pa.large_string())


def matchkey_composite(
    fields_with_chains: Sequence[tuple[Any, Sequence[str]]], sep: str = "||"
) -> Any:
    """build_matchkey_expr's composite: per-field chains (each field its OWN
    transform list -- unlike block_key's shared chain), single-field skips the
    join, multi-field concat null-propagates like pl.concat_str."""
    import pyarrow as pa
    import pyarrow.compute as pc

    cols = [matchkey_field_column(a, list(t)) for a, t in fields_with_chains]
    if len(cols) == 1:
        return cols[0]
    sep_scalar = pa.scalar(sep, type=pa.large_string())
    return pc.binary_join_element_wise(*cols, sep_scalar, null_handling="emit_null")


def ne_joined_column(field_arrs: Sequence[Any]) -> Any:
    """precompute_matchkey_transforms' derived-NE source join
    (matchkey.py:381-386): per-field Utf8 cast + fill_null("") + space-join.
    fill_null means the join NEVER null-propagates -- a missing part joins as
    the empty string, exactly like the Polars expression."""
    import pyarrow as pa
    import pyarrow.compute as pc

    filled = [pc.fill_null(cast_utf8(a), "") for a in field_arrs]
    if len(filled) == 1:
        return filled[0]
    sep_scalar = pa.scalar(" ", type=pa.large_string())
    return pc.binary_join_element_wise(*filled, sep_scalar, null_handling="emit_null")
