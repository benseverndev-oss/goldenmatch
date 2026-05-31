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


def _materialize_blocks_to_arrow(
    blocks: list,
    field_name: str,
    across_files_only: bool,
    source_lookup: dict[int, str] | None,
) -> "pa.Table | None":
    """Flatten all blocks into ONE Arrow table tagged by ``__block_key__``.

    This is the architectural pivot for Day-3 v2 / B1.5: instead of
    creating a SessionContext + table + UDF + dropping per block, we
    register a SINGLE big table once and let DataFusion's hash-join
    planner partition the (a.block_key = b.block_key AND a.id < b.id)
    self-join across all CPU cores. That's the query shape DataFusion
    is designed for and what bucket already does manually with
    ``partition_by(bucket)`` + per-bucket parallel cdist. Comparing
    these two is the apples-to-apples test the spec actually wanted.

    Returns ``None`` if no rows survive the per-block filters
    (across-files-only, height < 2) -- caller short-circuits.
    """
    import polars as _pl  # local import keeps the optional-extra pure

    block_keys: list[str] = []
    row_ids_chunks: list[list[int]] = []
    values_chunks: list[list[str]] = []

    for block in blocks:
        block_df = block.df.collect()
        if block_df.height < 2:
            continue
        if across_files_only and source_lookup:
            sources_in_block = block_df["__source__"].unique().to_list()
            if len(sources_in_block) < 2:
                continue
        n = block_df.height
        bk = str(block.block_key)
        # block_keys is a python list because pa.array(str * n) is faster
        # than pa.string_array.from_buffers tricks for our shapes.
        block_keys.extend([bk] * n)
        row_ids_chunks.append(block_df["__row_id__"].cast(_pl.Int64).to_list())
        values_chunks.append(block_df[field_name].cast(_pl.Utf8).to_list())

    if not row_ids_chunks:
        return None

    # Flatten in one pass each (faster than nested extend for >100K total).
    row_ids: list[int] = [r for chunk in row_ids_chunks for r in chunk]
    values: list[str | None] = [v for chunk in values_chunks for v in chunk]

    return pa.table({
        "__block_key__": pa.array(block_keys, type=pa.large_string()),
        "__row_id__": pa.array(row_ids, type=pa.int64()),
        "__value__": pa.array(values, type=pa.large_string()),
    })


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

    Architecture (Day-3 v2, single-context shape):
      1. Validate matchkey is in spike scope.
      2. Flatten all blocks into one Arrow table tagged by block_key.
      3. Register the table on ONE SessionContext, register one UDF.
      4. Run ONE SQL query:
           SELECT a.id, b.id, score(a.v, b.v) FROM data a
           JOIN data b ON a.block_key = b.block_key AND a.id < b.id
           WHERE score >= threshold
         DataFusion's hash-join planner partitions across CPU cores
         by block_key -- the same parallelism shape ``bucket`` builds
         manually with ``partition_by(bucket)`` + parallel cdist.
      5. Apply ``matched_pairs`` / ``source_lookup`` / ``target_ids``
         filters in Python on the result set.

    Day-1 used per-block SessionContext (bench-day3 measured 2.56x
    slower than bucket on the 100K fixture, dominated by per-block
    setup overhead). The single-context shape is what DataFusion is
    designed for; Day-3 v2 measures the same workload through that
    lens.
    """
    if not blocks:
        return []

    field_name, scorer_name, threshold = _validate_matchkey(mk)
    datafusion_mod = _ensure_datafusion()
    native_mod = _ensure_native()

    table = _materialize_blocks_to_arrow(
        blocks, field_name, across_files_only, source_lookup,
    )
    if table is None:
        return []

    ctx = datafusion_mod.SessionContext()
    udf = _make_score_udf(scorer_name, datafusion_mod, native_mod)
    ctx.register_udf(udf)
    udf_callable_name = f"_gm_score_{scorer_name}"
    ctx.register_record_batches("data", [table.to_batches()])

    # Self-join on (block_key, id_a < id_b). The inner SELECT computes
    # score once per pair; the outer SELECT filters. (DataFusion's
    # planner does NOT reliably dedup repeated UDF references when the
    # UDF appears in both SELECT and WHERE on the same query level --
    # nesting forces single evaluation.)
    sql = f"""
        SELECT id_a, id_b, score FROM (
            SELECT
                a.__row_id__ AS id_a,
                b.__row_id__ AS id_b,
                {udf_callable_name}(a.__value__, b.__value__) AS score
            FROM data a
            JOIN data b
              ON a.__block_key__ = b.__block_key__
             AND a.__row_id__ < b.__row_id__
        ) scored
        WHERE score >= {threshold}
    """
    result_table = ctx.sql(sql).to_arrow_table()

    if result_table.num_rows == 0:
        logger.info(
            "DataFusion backend: scored %d block(s), emitted 0 pair(s) "
            "above threshold %.3f using scorer=%s field=%s",
            len(blocks), threshold, scorer_name, field_name,
        )
        return []

    id_a_arr = result_table.column("id_a").to_pylist()
    id_b_arr = result_table.column("id_b").to_pylist()
    score_arr = result_table.column("score").to_pylist()

    exclude_frozen = frozenset(matched_pairs)
    all_pairs: list[tuple[int, int, float]] = []
    for a, b, s in zip(id_a_arr, id_b_arr, score_arr, strict=True):
        a_i = int(a)
        b_i = int(b)
        key = (a_i, b_i) if a_i < b_i else (b_i, a_i)
        if key in exclude_frozen:
            continue
        if across_files_only and source_lookup:
            if source_lookup.get(a_i) == source_lookup.get(b_i):
                continue
        if target_ids is not None:
            # Keep pair only if EXACTLY ONE side is in target_ids
            # (matches score_blocks_parallel's filter semantics).
            if (a_i in target_ids) == (b_i in target_ids):
                continue
        all_pairs.append((key[0], key[1], float(s)))
        matched_pairs.add(key)

    logger.info(
        "DataFusion backend: scored %d block(s), emitted %d pair(s) "
        "above threshold %.3f using scorer=%s field=%s",
        len(blocks), len(all_pairs), threshold, scorer_name, field_name,
    )
    return all_pairs
