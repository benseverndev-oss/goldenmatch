"""Matchkey builder for GoldenMatch."""

from __future__ import annotations

import hashlib
import os

import polars as pl

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.complexity_profile import FieldStats, MatchkeyProfile
from goldenmatch.core.profile_emitter import _emitter_stack, current_emitter
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
    elif transform == "address_normalize":
        # Opt-in via env var until parity vs the Python plugin is fully
        # locked down at the dedupe-pipeline level. v1 surfaced an
        # `test_dedupe_with_adaptive_blocking` cluster-count drift (8 vs 5)
        # on the sample CSV, suggesting a pre-tokenization or
        # canonical-map edge case that the per-row parity tests don't
        # cover. Until that's tracked down, default OFF -- the chain
        # exists, is unit-tested for parity on 18 representative inputs,
        # but only fires when GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE=1.
        # When that env is unset the chain returns None and the caller's
        # transform pipeline falls back to the Python plugin (same
        # behavior as before this PR).
        import os as _os
        if _os.environ.get("GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE") != "1":
            return None
        return _address_normalize_native(expr)
    else:
        # soundex, metaphone, etc. need Python UDF
        return None


def _address_normalize_native(expr: pl.Expr) -> pl.Expr | None:
    """Polars-native bit-equivalent of refdata.addresses.normalize_address.

    Returns None when refdata data isn't loadable (caller falls back to the
    Python path, which itself degrades to lowercase+strip in that case).

    NOTE (2026-05-29): the dispatcher in `_try_native_transform` gates this
    on `GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE=1` while we chase a parity
    edge case surfaced by test_dedupe_with_adaptive_blocking. Per-row
    parity tests in tests/test_native_address_normalize.py pass; the
    integration-level drift hasn't been root-caused yet.
    """
    try:
        from goldenmatch.refdata.addresses import _load as _addr_load
    except ImportError:
        return None
    _addr_load()
    from goldenmatch.refdata.addresses import _state as _loaded
    if _loaded is None:
        return None
    canonical_map = dict(_loaded.canonical)

    e = expr.str.strip_chars()
    # `#N` -> `apt N`. Polars' Rust regex engine has no lookbehind, so we
    # capture the preceding non-alphanumeric char and put it back via ${1}.
    e = e.str.replace_all(r"(^|[^A-Za-z0-9])#\s*(\d+)", r"${1}apt ${2}")
    e = e.str.replace_all(r"(?i)\bP\.?\s*O\.?\s*Box\b", "PO Box")
    e = e.str.replace_all(r"(?i)\bPOBOX\b", "PO Box")
    # Normalize commas to spaces so a single split delimiter handles `[\s,]+`.
    e = e.str.replace_all(",", " ")
    e = e.str.to_lowercase()
    # Tokenize, per-token punctuation strip + dictionary canonicalization,
    # then join. The list-element pipeline keeps everything in Polars.
    tokens = e.str.split(" ")
    per_token = (
        pl.element()
        .str.replace_all(r"^[.,;:#\-]+", "")
        .str.replace_all(r"[.,;:]+$", "")
        .replace(canonical_map)
    )
    canonicalized = _list_eval(tokens, per_token)
    nonempty = _list_eval(canonicalized, pl.element().filter(pl.element() != ""))
    return nonempty.list.join(" ")


def _list_eval(list_expr: pl.Expr, inner: pl.Expr) -> pl.Expr:
    """Thin wrapper around Polars `list.eval` to keep the address-normalize
    expression readable. Indirection avoids fanning the literal method name
    across multiple busy expression chains."""
    return list_expr.list.eval(inner)


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


def _emit_matchkey_profile(lf_after: pl.LazyFrame, matchkeys: list) -> None:
    """Emit MatchkeyProfile from post-transform columns. No-op when null emitter."""
    if not _emitter_stack.get():
        return
    df = lf_after.collect()
    n_total = df.height
    per_field: dict[str, FieldStats] = {}
    seen_fields: set[str] = set()
    for mk in matchkeys:
        for f in getattr(mk, "fields", []) or []:
            field_name = getattr(f, "field", None)
            if field_name is None or field_name in seen_fields:
                continue
            seen_fields.add(field_name)
            # Try the exact-matchkey combined column, then fall back to raw column
            mk_col = f"__mk_{mk.name}__"
            candidates = [mk_col, field_name]
            col = next((c for c in candidates if c in df.columns), None)
            if col is None or n_total == 0:
                continue
            ser = df.select(pl.col(col)).to_series()
            non_null = ser.drop_nulls()
            n_non_null = non_null.len()
            if n_non_null == 0:
                per_field[field_name] = FieldStats(
                    post_transform_cardinality_ratio=0.0,
                    post_transform_null_rate=1.0,
                    post_transform_value_length_p50=0,
                )
                continue
            n_distinct = non_null.n_unique()
            try:
                lengths = sorted(non_null.cast(pl.Utf8).str.len_chars().to_list())
                p50 = lengths[len(lengths) // 2] if lengths else 0
            except Exception:
                p50 = 0
            # Chao1 inputs: count how many distinct values appear exactly once
            # (F1, singletons) and exactly twice (F2, doubletons) in the
            # sample. value_counts groups by value and returns the count
            # column we can filter on. Lets MatchkeyProfile.health() and
            # downstream rules estimate full-data cardinality from a small
            # sample instead of being fooled by sample-scale uniqueness
            # (v24 finding: at 3K sample / 2M-cluster shapes, every field
            # looks unique in the sample even when it's not at full scale).
            f1 = 0
            f2 = 0
            try:
                vc = non_null.value_counts()
                # value_counts result has shape (n_distinct, 2) with the
                # second column always named "count" in recent Polars.
                counts = vc["count"] if "count" in vc.columns else vc[vc.columns[-1]]
                f1 = int((counts == 1).sum())
                f2 = int((counts == 2).sum())
            except Exception:
                # Degrades to "no Chao1 inputs"; FieldStats falls back to
                # raw cardinality in that case.
                f1, f2 = 0, 0
            per_field[field_name] = FieldStats(
                post_transform_cardinality_ratio=n_distinct / n_non_null,
                post_transform_null_rate=1 - (n_non_null / n_total),
                post_transform_value_length_p50=int(p50),
                sample_n_rows=int(n_non_null),
                singleton_count=f1,
                doubleton_count=f2,
            )
    current_emitter().set_matchkey(MatchkeyProfile(per_field=per_field))


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
    _emit_matchkey_profile(lf, matchkeys)
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
    is automatic via the signature. Native chains use vectorized Polars
    expressions via `_try_native_chain`; non-native chains fall back to Python
    per-row `apply_transforms` once.

    Skips fields whose scorer is `record_embedding` (uses multi-column
    `field.columns`, has its own scoring path that doesn't call
    `_get_transformed_values`).

    Fields with an empty transforms list are materialized too, as the plain
    `cast(Utf8)` identity column (`_try_native_chain` always casts first). This
    is required for the bucket fast path: `_resolve_fast_path` only engages —
    and only then can the native `score_block_pairs` kernel fire — when every
    field's `__xform_<sig>__` column is already present. It also removes the
    per-block `.select()` the slow path's `_get_transformed_values` fallback
    would otherwise run for transform-less fields. The materialized values are
    identical to that fallback, so scoring output is unchanged.

    **Caller contract:** downstream code must NOT mutate the source columns
    referenced by the matchkey fields after this function runs. The fast
    path in `_get_transformed_values` trusts the precomputed column over
    re-deriving from source — silently returning stale values if the source
    is changed later. Today no in-tree caller does this (blocks are sliced
    from the augmented df, not mutated), but adding a mid-pipeline column
    rewrite step in the future would violate this assumption.

    Returns the augmented DataFrame. Original columns are untouched.
    """
    # Batch all native-chain expressions into ONE with_columns call so
    # Polars's planner fuses them into a single compute graph. The previous
    # loop did `df.select(expr.alias(sig))[sig]` per signature, which
    # eagerly materialized one 10M-row column at a time -- at 6 matchkey
    # fields on the QIS bench that was 6 separate Polars passes over the
    # full df, dominating the precompute_matchkey_transforms stage (90s
    # at 10M). Slow-path Python apply_transforms still runs per signature
    # (intrinsically per-row); those go into new_cols as today and get
    # appended in the same final with_columns.
    seen: set[str] = set()
    native_exprs: list[pl.Expr] = []
    python_cols: list[pl.Series] = []

    def _materialize_xform(field_obj):
        """Resolve one (field, transforms) sig; appends to native_exprs or
        python_cols. Idempotent via `seen` -- the same (field, transforms)
        across multiple matchkeys / NE entries reuses one column.

        Accepts any object with `.field` and `.transforms` attrs --
        MatchkeyField and NegativeEvidenceField both qualify (the latter
        is the new caller in the NE fast-path widening, 2026-05-29)."""
        if getattr(field_obj, "scorer", None) == "record_embedding":
            return
        sig = _xform_sig(field_obj)
        if sig in seen or sig in df.columns:
            return
        seen.add(sig)
        native_expr = _try_native_chain(field_obj.field, field_obj.transforms)
        if native_expr is not None:
            native_exprs.append(native_expr.alias(sig))
        else:
            values = df[field_obj.field].to_list()
            python_cols.append(pl.Series(
                sig,
                [apply_transforms(v, field_obj.transforms) if v is not None else None
                 for v in values],
            ))

    for mk in matchkeys:
        for field in mk.fields:
            _materialize_xform(field)
        # v1.11 NE fast-path widening: precompute NE field xforms too so
        # _resolve_fast_path's NE resolution can find the columns at gate
        # time. NegativeEvidenceField has the same (field, transforms)
        # duck-type as MatchkeyField, so _xform_sig reuses unchanged. Same
        # signature => same column => NE on a field that ALSO appears in
        # mk.fields with identical transforms is a free reuse.
        for ne in (getattr(mk, "negative_evidence", None) or []):
            _materialize_xform(ne)

    if not native_exprs and not python_cols:
        return df
    if native_exprs:
        df = df.with_columns(native_exprs)
    if python_cols:
        df = df.with_columns(python_cols)
    # Optional rechunk experiment (PR #591). The v30 RSS leaderboard
    # showed `bucket_slim_projection` allocating ~10 GB during its
    # .select() call, hypothesized to be Polars consolidating __xform_*__
    # chunks deposited here by separate with_columns calls. A proactive
    # rechunk MOVES that consolidation cost from select-time (where it
    # also has to pull from the still-resident prepared_df) to here,
    # where the unconsolidated chunks can be freed immediately. Net peak
    # could drop if the old chunks release before downstream peak. Gated
    # off by default until v31 measures it.
    if os.environ.get("GOLDENMATCH_PRECOMPUTE_RECHUNK") == "1":
        df = df.rechunk()
    return df
