"""Matchkey builder for GoldenMatch."""

from __future__ import annotations

import hashlib
import re

import polars as pl

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.utils.transforms import apply_transforms


def _try_native_chain(column: str, transforms: list[str]) -> pl.Expr | None:
    """Try to build a fully native Polars expression chain for transforms.

    Returns a Polars expression if ALL transforms are natively expressible,
    or None if any requires a Python UDF.
    """
    expr = pl.col(column).cast(pl.Utf8)
    for t in transforms:
        result = _try_native_transform(expr, t)
        if result is None:
            return None
        expr = result
    return expr


def _try_native_transform(expr: pl.Expr, transform: str) -> pl.Expr | None:
    """Try to apply a transform using native Polars expressions.

    Returns the transformed expression, or None if the transform
    requires a Python UDF (map_elements).
    """
    if transform == "lowercase":
        return expr.str.to_lowercase()
    elif transform == "uppercase":
        return expr.str.to_uppercase()
    elif transform == "strip":
        return expr.str.strip_chars()
    elif transform.startswith("substring:"):
        parts = transform.split(":")
        start = int(parts[1])
        length = int(parts[2]) - start
        return expr.str.slice(start, length)
    elif transform == "normalize_whitespace":
        return expr.str.replace_all(r"\s+", " ").str.strip_chars()
    elif transform == "strip_all":
        return expr.str.replace_all(r"\s+", "")
    elif transform == "digits_only":
        return expr.str.replace_all(r"[^0-9]", "")
    elif transform == "alpha_only":
        return expr.str.replace_all(r"[^a-zA-Z]", "")
    else:
        # soundex, metaphone, etc. need Python UDF
        return None


def _build_field_expr_native(field_name: str, transforms: list[str]) -> pl.Expr | None:
    """Try to build a fully native Polars expression for a field's transforms.

    Returns None if any transform requires map_elements.
    """
    expr = pl.col(field_name).cast(pl.Utf8)
    for t in transforms:
        result = _try_native_transform(expr, t)
        if result is None:
            return None
        expr = result
    return expr


def build_matchkey_expr(mk: MatchkeyConfig) -> pl.Expr:
    """Build a Polars expression for a matchkey.

    For exact matchkeys: transforms each field using native Polars expressions
    when possible, falling back to map_elements + apply_transforms for complex
    transforms (soundex, metaphone). Concatenates with "||" separator.
    Returns expr aliased as ``__mk_{mk.name}__``.

    For weighted matchkeys: returns pl.lit(None) placeholder (fuzzy scoring handled
    in scorer).

    Args:
        mk: The matchkey configuration.

    Returns:
        A Polars expression producing the matchkey column.
    """
    alias = f"__mk_{mk.name}__"

    if mk.type == "weighted":
        return pl.lit(None).alias(alias)

    # Exact matchkey: transform each field, then concatenate with "||"
    field_exprs = []
    for f in mk.fields:
        if f.transforms:
            # Try native Polars expressions first
            native_expr = _build_field_expr_native(f.field, f.transforms)
            if native_expr is not None:
                expr = native_expr
            else:
                # Fall back to map_elements for complex transforms
                expr = pl.col(f.field).map_elements(
                    lambda val, transforms=f.transforms: apply_transforms(val, transforms),
                    return_dtype=pl.Utf8,
                )
        else:
            expr = pl.col(f.field).cast(pl.Utf8)
        field_exprs.append(expr)

    if len(field_exprs) == 1:
        return field_exprs[0].alias(alias)

    return pl.concat_str(field_exprs, separator="||").alias(alias)


def compute_matchkeys(
    lf: pl.LazyFrame, matchkeys: list[MatchkeyConfig]
) -> pl.LazyFrame:
    """Add matchkey columns for all exact matchkeys.

    Args:
        lf: Input LazyFrame.
        matchkeys: List of matchkey configurations.

    Returns:
        LazyFrame with additional matchkey columns for each exact matchkey.
    """
    exprs = []
    for mk in matchkeys:
        if mk.type == "exact":
            exprs.append(build_matchkey_expr(mk))
    if exprs:
        lf = lf.with_columns(exprs)
    return lf


def _xform_sig(field: MatchkeyField) -> str:
    """Stable, process-independent signature for a (field, transforms) pair.

    Uses blake2b rather than Python's salted hash() so the resulting column
    name is deterministic across processes — makes debugging dumps diffable
    and avoids spooky cross-run differences in error messages.
    """
    digest = hashlib.blake2b(
        repr(field.transforms).encode(), digest_size=8
    ).hexdigest()
    return f"__xform_{field.field}_{digest}__"


def precompute_matchkey_transforms(
    df: pl.DataFrame, matchkeys: list[MatchkeyConfig]
) -> pl.DataFrame:
    """Add one __xform_<sig>__ column per unique (field, transforms) signature.

    Same field+transforms across multiple matchkeys reuses one column — dedup
    is automatic via the signature. Native chains use _try_native_chain (Rust);
    non-native chains fall back to Python per-row apply_transforms once.

    Skips fields whose scorer is `record_embedding` (uses multi-column
    field.columns, has its own scoring path that doesn't call
    _get_transformed_values).

    Skips fields with empty transforms list — nothing to precompute, and
    _get_transformed_values' legacy path is already a single to_list() call.

    Returns the augmented DataFrame. Original columns are untouched.
    """
    seen: set[str] = set()
    new_cols: list[pl.Series] = []
    for mk in matchkeys:
        for field in mk.fields:
            if field.scorer == "record_embedding":
                continue
            if not field.transforms:
                continue
            sig = _xform_sig(field)
            if sig in seen or sig in df.columns:
                continue
            seen.add(sig)

            native_expr = _try_native_chain(field.field, field.transforms)
            if native_expr is not None:
                col = df.select(native_expr.alias(sig))[sig]
            else:
                values = df[field.field].to_list()
                col = pl.Series(
                    sig,
                    [apply_transforms(v, field.transforms) if v is not None else None
                     for v in values],
                )
            new_cols.append(col)

    if not new_cols:
        return df
    return df.with_columns(new_cols)
