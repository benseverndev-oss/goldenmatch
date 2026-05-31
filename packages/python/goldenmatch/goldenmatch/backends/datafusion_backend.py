"""DataFusion block-scoring backend.

Spike — see ``docs/superpowers/specs/2026-05-30-datafusion-backend-spike-design.md``
(gitignored). Routes block scoring through Apache DataFusion's columnar
query engine, with native scorers wrapped as vectorized Arrow-batch
Python UDFs that delegate to ``goldenmatch._native``.

Day 2 scope: single-field ``weighted`` matchkey, scorer in
``{"jaro_winkler", "levenshtein", "token_sort"}``. Anything outside
that scope raises ``NotImplementedError`` — the spike does NOT yet
cover multi-field weighted, exact, probabilistic, or embedding
scorers; falling back to the parallel scorer for those is the
caller's responsibility.

Opt-in via ``config.backend="datafusion"``. The optional extra is
``goldenmatch[datafusion]`` (pulls ``datafusion>=44``).
"""
from __future__ import annotations

import logging
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)


# Scorers supported in the Day-2 spike. Each maps to a callable that
# takes two strings and returns a float in [0, 1]. The native module
# is required — bench numbers from the parallel path with jellyfish
# fallback would be misleading.
_SUPPORTED_SCORERS = ("jaro_winkler", "levenshtein", "token_sort")


def _ensure_datafusion():
    """Lazy import so the optional extra stays truly optional."""
    try:
        import datafusion
        return datafusion
    except ImportError as e:
        raise ImportError(
            "DataFusion backend requires datafusion. Install with:\n"
            "    pip install goldenmatch[datafusion]"
        ) from e


def _ensure_native():
    """Hard-require the native module. The whole premise of the
    DataFusion backend is native scoring inside DataFusion's planner;
    falling back to jellyfish would invalidate any bench result."""
    try:
        import goldenmatch._native as native
        return native
    except ImportError as e:
        raise ImportError(
            "DataFusion backend requires the compiled native module. "
            "Build it with: python scripts/build_native.py\n"
            "(Then re-install: pip install -e packages/python/goldenmatch)"
        ) from e


def _validate_matchkey(mk: Any) -> tuple[str, str, float]:
    """Validate that ``mk`` is in the spike-supported subset, and
    return ``(field_name, scorer_name, threshold)``.

    Raises:
        NotImplementedError if the matchkey shape is outside the
        spike scope. Callers should treat this as a signal to fall
        back to the parallel backend, not as a bug.
    """
    if getattr(mk, "type", None) != "weighted":
        raise NotImplementedError(
            f"DataFusion backend (spike) only supports type='weighted' "
            f"matchkeys; got type={mk.type!r}. Use a different backend."
        )

    fields = list(getattr(mk, "fields", []) or [])
    if len(fields) != 1:
        raise NotImplementedError(
            f"DataFusion backend (spike) only supports single-field "
            f"weighted matchkeys; got {len(fields)} fields. "
            f"Multi-field is a follow-up."
        )

    field = fields[0]
    scorer_name = getattr(field, "scorer", None)
    if scorer_name not in _SUPPORTED_SCORERS:
        raise NotImplementedError(
            f"DataFusion backend (spike) only supports scorers "
            f"{_SUPPORTED_SCORERS}; got scorer={scorer_name!r}."
        )

    field_name = field.resolved_field if hasattr(field, "resolved_field") else field.field
    threshold = mk.fuzzy_threshold if hasattr(mk, "fuzzy_threshold") else mk.threshold
    if threshold is None:
        raise ValueError(
            "DataFusion backend requires a non-None matchkey threshold "
            "(weighted matchkeys must set threshold per the schema validator)."
        )
    return field_name, scorer_name, float(threshold)


def _make_score_udf(scorer_name: str, datafusion_mod, native_mod):
    """Build a vectorized Arrow-batch UDF that calls into the native
    scorer once per record batch.

    Note on Path B1 vs B2: this is B1 (vectorized Python wrapper around
    pyo3 native). Python pays per-batch overhead (~1 call per ~8K rows
    depending on DataFusion's batch size). If Day-3 cProfile shows that
    overhead is material, we drop to B2 — a true Rust ScalarUDF
    registered via datafusion-python's PyCapsule FFI. The
    PyCapsule path is documented in ``datafusion.udf`` itself.
    """
    if scorer_name == "jaro_winkler":
        scorer = native_mod.jaro_winkler_similarity
    elif scorer_name == "levenshtein":
        scorer = native_mod.levenshtein_similarity
    elif scorer_name == "token_sort":
        # native token_sort_ratio returns 0-100; normalize to 0-1 here
        # to match jellyfish/rapidfuzz convention used elsewhere.
        _raw = native_mod.token_sort_ratio
        def scorer(a: str, b: str) -> float:
            return _raw(a, b) / 100.0
    else:  # defensive; _validate_matchkey already gates this
        raise NotImplementedError(scorer_name)

    def _batch_fn(left: pa.Array, right: pa.Array) -> pa.Array:
        lefts = left.to_pylist()
        rights = right.to_pylist()
        out = [scorer(a or "", b or "") for a, b in zip(lefts, rights, strict=True)]
        return pa.array(out, type=pa.float64())

    return datafusion_mod.udf(
        _batch_fn,
        input_fields=[pa.string(), pa.string()],
        return_field=pa.float64(),
        volatility="immutable",
        name=f"_gm_score_{scorer_name}",
    )


def _score_one_block_datafusion(
    block: Any,
    field_name: str,
    udf_callable_name: str,
    threshold: float,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]],
    ctx,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
) -> list[tuple[int, int, float]]:
    """Score one block via DataFusion. Returns canonicalized
    ``(min_id, max_id, score)`` tuples above threshold.
    """
    block_df = block.df.collect()
    if block_df.height < 2:
        return []

    if across_files_only and source_lookup:
        sources_in_block = block_df["__source__"].unique().to_list()
        if len(sources_in_block) < 2:
            return []

    # Build a 2-column Arrow table: __row_id__ (int64), <field> (string).
    # We deliberately don't ship the whole block_df into DataFusion --
    # the only columns the spike's scoring SQL needs are the row id
    # and the field being scored. Extra columns would inflate
    # serialization cost without affecting the result.
    import polars as _pl  # local import keeps optional-extra purity
    row_ids = block_df["__row_id__"].cast(_pl.Int64).to_arrow()
    values = block_df[field_name].cast(_pl.Utf8).to_arrow()
    table = pa.table({"__row_id__": row_ids, field_name: values})

    # Per-block table name keeps blocks isolated; we deregister at end
    # of the call to free DataFusion's internal state.
    table_name = f"_gm_block_{id(block)}"
    ctx.register_record_batches(table_name, [table.to_batches()])
    try:
        # Self-join on (a.id < b.id) for canonical pair ordering,
        # score, threshold filter. DataFusion's planner picks the
        # join strategy (hash vs nested-loop) -- our spike just hands
        # it the SQL and inspects the result.
        sql = f"""
            SELECT
                a.__row_id__ AS id_a,
                b.__row_id__ AS id_b,
                {udf_callable_name}(a.{field_name}, b.{field_name}) AS score
            FROM {table_name} a
            JOIN {table_name} b ON a.__row_id__ < b.__row_id__
            WHERE {udf_callable_name}(a.{field_name}, b.{field_name}) >= {threshold}
        """
        df = ctx.sql(sql)
        result_table = df.to_arrow_table()
    finally:
        try:
            ctx.deregister_table(table_name)
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            pass

    if result_table.num_rows == 0:
        return []

    id_a_arr = result_table.column("id_a").to_pylist()
    id_b_arr = result_table.column("id_b").to_pylist()
    score_arr = result_table.column("score").to_pylist()

    pairs: list[tuple[int, int, float]] = []
    for a, b, s in zip(id_a_arr, id_b_arr, score_arr, strict=True):
        a_i = int(a)
        b_i = int(b)
        key = (a_i, b_i) if a_i < b_i else (b_i, a_i)
        if key in exclude_pairs:
            continue
        if across_files_only and source_lookup:
            if source_lookup.get(a_i) == source_lookup.get(b_i):
                continue
        pairs.append((key[0], key[1], float(s)))
    return pairs


def score_blocks_datafusion(
    blocks: list,
    mk: Any,
    matched_pairs: set[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int, float]]:
    """Score all blocks via DataFusion. Drop-in for
    ``score_blocks_parallel`` modulo the spike-scope constraints in
    ``_validate_matchkey`` (single-field weighted, supported scorer).

    See module docstring for spike scope. Anything outside that scope
    raises ``NotImplementedError`` so callers can route to a
    different backend without silent fallback (which would taint
    bench numbers).
    """
    if not blocks:
        return []

    field_name, scorer_name, threshold = _validate_matchkey(mk)
    datafusion_mod = _ensure_datafusion()
    native_mod = _ensure_native()

    # One SessionContext + one UDF registration per call. SessionContext
    # is cheap to create (no daemon, no warm-up), and reusing one across
    # all blocks of this call lets DataFusion amortize any internal
    # caches across blocks. Day-3 bench should confirm this isn't a
    # bottleneck; if it is, the next architectural step is "one
    # SessionContext, one big Arrow table with all blocks tagged by
    # __block_key__, one GROUP BY query" -- which is the shape
    # DataFusion is actually designed for and the spec's secondary
    # architectural lever.
    ctx = datafusion_mod.SessionContext()
    udf = _make_score_udf(scorer_name, datafusion_mod, native_mod)
    ctx.register_udf(udf)
    udf_callable_name = f"_gm_score_{scorer_name}"

    exclude_frozen = frozenset(matched_pairs)

    all_pairs: list[tuple[int, int, float]] = []
    for block in blocks:
        block_pairs = _score_one_block_datafusion(
            block,
            field_name=field_name,
            udf_callable_name=udf_callable_name,
            threshold=threshold,
            exclude_pairs=exclude_frozen,
            ctx=ctx,
            across_files_only=across_files_only,
            source_lookup=source_lookup,
        )
        if target_ids is not None:
            block_pairs = [
                (a, b, s) for a, b, s in block_pairs
                if (a in target_ids) != (b in target_ids)
            ]
        all_pairs.extend(block_pairs)
        for a, b, _s in block_pairs:
            matched_pairs.add((a, b))

    logger.info(
        "DataFusion backend: scored %d block(s), emitted %d pair(s) "
        "above threshold %.3f using scorer=%s field=%s",
        len(blocks), len(all_pairs), threshold, scorer_name, field_name,
    )
    return all_pairs
