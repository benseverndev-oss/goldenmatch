"""In-process bucketed block scorer.

Architectural pivot from the per-block LazyFrame model:

  OLD (score_blocks_parallel / score_blocks_duckdb):
    build_blocks(combined_lf, blocking) -> list[BlockResult]
      where each BlockResult.df is a `combined_lf.filter(blocking_key == K)`
      LazyFrame. At 5M rows / 1.67M blocks of 3 rows each, the LIST of
      1.67M filter-LazyFrames + any per-block `.collect()`/`.select()` chains
      explode Polars arena memory. Documented in heartbeats:
      runs 25998537828, 26000789629, 26002766443, 26004842882, 26006853280,
      26008682481, 26012579494 -- all hung at 62.99 GB RSS plateau on Linux
      without ever reaching real scoring.

  NEW (score_buckets):
    prepared_df (eager) + blocking_config -> in one Polars pass:
      with_columns(__block_key__ = key_expr, __bucket__ = hash(__block_key__) % N)
    -> partition_by("__bucket__", as_dict=True)   # ≤ N eager bucket dfs
    -> partition_by("__block_key__", as_dict=True) within each bucket
    -> _score_one_block on each per-block eager df

    No LazyFrames carrying filter expressions. No materialization of millions
    of small frames. Two partition_by operations + N rapidfuzz calls.

Hard invariant: at scale, this module must never call ``.collect()`` on a
filter-LazyFrame. The single eager materialization happens once via
``prepared_df = combined_lf.collect()`` at the pipeline call site BEFORE
this scorer runs.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import polars as pl

from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig
from goldenmatch.core._native_loader import native_enabled, native_module
from goldenmatch.core.bench import record_metrics, stage
from goldenmatch.core.blocker import _build_block_key_expr

logger = logging.getLogger(__name__)

# Scorers the native fast-path kernel (goldenmatch._native.score_block_pairs)
# implements, with the ids it expects. A field whose scorer isn't here forces
# the Python per-pair loop for that bucket.
_NATIVE_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3,
}


BUCKET_HASH_SEED = 0xC2B5C0BBE7ED5E5D
"""Same constant as goldenmatch.distributed.record_store. Deterministic
xxHash seed so block_key -> bucket assignment is stable across runs."""


def _default_n_buckets() -> int:
    """Default bucket count. min(cpu_count() * 4, 1024). Same heuristic as
    Component 2 v2's materialize_bucketed_blocks."""
    return min((os.cpu_count() or 4) * 4, 1024)


def _resolve_score_pair_callable(scorer_name: str) -> Any:
    """Return a (str_a, str_b) -> float | None callable for a scorer name.

    Used by the bucket scorer's fast path so per-pair work skips the
    PluginRegistry / dispatch overhead that ``_fuzzy_score_matrix`` does
    per (block x field). None when the scorer isn't fast-path safe
    (embedding, ensemble, record_embedding, unknown).
    """
    if scorer_name == "jaro_winkler":
        from rapidfuzz.distance import JaroWinkler
        return JaroWinkler.similarity
    if scorer_name == "levenshtein":
        from rapidfuzz.distance import Levenshtein
        return Levenshtein.normalized_similarity
    if scorer_name == "token_sort":
        from rapidfuzz.fuzz import token_sort_ratio
        return lambda a, b: token_sort_ratio(a, b) / 100.0
    if scorer_name == "exact":
        return lambda a, b: 1.0 if a == b else 0.0
    if scorer_name == "soundex_match":
        # Pure-Python jellyfish.soundex; per-pair binary match. Identical
        # to the matrix path's soundex_match (core/scorer.py:88), just
        # one call at a time instead of cdist-batched.
        import jellyfish as _jf
        return lambda a, b: 1.0 if _jf.soundex(a) == _jf.soundex(b) else 0.0
    if scorer_name == "dice":
        # Delegate to the existing per-pair implementation in core/scorer.py
        # (_dice_score_single, bigram set Dice coefficient). Matrix path is
        # vectorized via _dice_score_matrix; per-pair is the same coefficient
        # one pair at a time. Unblocks fast path for workloads where
        # auto-config or explicit config picks dice on a string field.
        from goldenmatch.core.scorer import _dice_score_single
        return _dice_score_single
    if scorer_name == "jaccard":
        from goldenmatch.core.scorer import _jaccard_score_single
        return _jaccard_score_single
    if scorer_name == "ensemble":
        # The matrix-path ensemble (scorer.py:343-348) is max(jw, ts, sx*0.8)
        # where sx = soundex_match. None of those needs ML/network -- they're
        # all pure-string transforms. Per-pair version composes the same
        # three scorers under max. Bit-equivalent to the matrix path within
        # rapidfuzz tolerance (the matrix path uses cdist for vectorized
        # batches; per-pair uses the same rapidfuzz primitives one call at a
        # time). Unblocks the bucket fast path on matchkeys auto-config
        # produced via _pick_scorer_for_column's "other -> ensemble" rule.
        import jellyfish as _jf
        from rapidfuzz.distance import JaroWinkler as _Jw
        from rapidfuzz.fuzz import token_sort_ratio as _ts
        def _ensemble_pair(a: str, b: str) -> float:
            jw = _Jw.similarity(a, b)
            ts = _ts(a, b) / 100.0
            sx = 0.8 if _jf.soundex(a) == _jf.soundex(b) else 0.0
            return max(jw, ts, sx)
        return _ensemble_pair
    if scorer_name in ("embedding", "record_embedding"):
        # Still model-backed; not fast-path eligible.
        return None
    # (dice / jaccard / soundex_match handled above)
    # Plugin scorer -- accept only when it exposes ``score_pair``.
    try:
        from goldenmatch.plugins.registry import PluginRegistry
        plugin = PluginRegistry.instance().get_scorer(scorer_name)
    except Exception:
        return None
    if plugin is None:
        return None
    fn = getattr(plugin, "score_pair", None)
    return fn  # may itself be None for matrix-only plugins


def _resolve_fast_path(
    mk: MatchkeyConfig,
    prepared_df: pl.DataFrame,
    *,
    across_files_only: bool,
    source_lookup: dict[int, str] | None,
    target_ids: set[int] | None,
) -> tuple[float, float, list[tuple[str, float, Any, str]]] | None:
    """Decide whether mk is fast-path eligible and pre-resolve field specs.

    Returns (threshold, total_weight, [(xform_col, weight, score_fn), ...])
    when eligible, else None. Resolution is done ONCE at score_buckets entry
    so per-pair work never touches the PluginRegistry, _get_transformed_values,
    or scorer-name dispatch.

    Eligibility gates (conservative — fall back to find_fuzzy_matches for
    anything more complex):
      - mk.type == "weighted"
      - mk.threshold set
      - no negative_evidence
      - no rerank / LLM
      - across_files_only=False, target_ids=None (dedupe single-source case)
      - every field resolves to a score_pair callable via
        _resolve_score_pair_callable AND has its xform column precomputed
    """
    from goldenmatch.core.matchkey import _xform_sig

    if mk.type != "weighted":
        return None
    if mk.threshold is None:
        return None
    if getattr(mk, "negative_evidence", None):
        return None
    if getattr(mk, "rerank", False):
        return None
    if getattr(mk, "llm", None):
        return None
    if across_files_only or source_lookup or target_ids is not None:
        return None
    if not mk.fields:
        return None

    field_specs: list[tuple[str, float, Any, str]] = []
    total_weight = 0.0
    for f in mk.fields:
        scorer = getattr(f, "scorer", None)
        weight = getattr(f, "weight", None)
        if scorer is None or weight is None:
            return None
        fn = _resolve_score_pair_callable(scorer)
        if fn is None:
            return None
        xform_col = _xform_sig(f)
        if xform_col not in prepared_df.columns:
            return None
        field_specs.append((xform_col, float(weight), fn, scorer))
        total_weight += float(weight)
    if total_weight <= 0:
        return None
    return (float(mk.threshold), total_weight, field_specs)


def score_buckets(
    prepared_df: pl.DataFrame,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    n_buckets: int | None = None,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int, float]]:
    """Score all blocks via hash-bucketed partition_by, no per-block LazyFrame.

    Args:
        prepared_df: Eager Polars DataFrame, already materialized. Must
            contain ``__row_id__`` and all columns referenced by ``mk`` +
            ``blocking_config``.
        blocking_config: Source for the block-key expression.
            ``keys[0]`` is used; multi-key blocking is not supported in
            bucket mode v1.
        mk: Matchkey configuration.
        matched_pairs: Set of already-matched (min_id, max_id) pairs;
            mutated in-place as new pairs are emitted (mirrors
            score_blocks_parallel's contract).
        n_buckets: Hash bucket count. None -> ``min(cpu_count() * 4, 1024)``.
        across_files_only: Filter to cross-source pairs only.
        source_lookup: Row ID -> source name mapping.
        target_ids: For match mode -- filter to target/ref cross pairs.

    Returns:
        All fuzzy pairs as (id_a, id_b, score) tuples.
    """
    if prepared_df.height == 0:
        return []
    if not blocking_config.keys:
        return []

    if n_buckets is None:
        n_buckets = _default_n_buckets()

    # Diag prints (flushed) so we can see substep timing on runner heartbeats
    # independent of the bench stage recorder, which only logs CLOSED stages.
    # Three 5M Linux runs hung mid-score_buckets with no substage closing;
    # these prints expose the actual hang line.
    _t0 = time.perf_counter()
    print(f"[score_buckets] entry: prepared_df.height={prepared_df.height} n_buckets={n_buckets}", flush=True)

    key_expr = _build_block_key_expr(blocking_config.keys[0])
    print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: key_expr built", flush=True)

    with stage("bucket_assign"):
        _ta = time.perf_counter()
        keyed = prepared_df.with_columns(key_expr)
        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: keyed (with_columns key_expr) in {time.perf_counter()-_ta:.2f}s", flush=True)

    # #422 fix 1: small-block fast path. When prepared_df.height < n_buckets,
    # the hash + partition_by step always collapses to 1 non-empty bucket
    # (every row hashes into the same bucket-output because most buckets
    # are empty by pigeonhole). The bookkeeping is pure overhead. Skip
    # straight to treating `keyed` as the single bucket and scoring.
    # On the streaming-block sync caller, this hits on every per-block
    # invocation (block size ~8, n_buckets default 32-128).
    if prepared_df.height < n_buckets:
        bucketed = keyed
        # Wrap in a single-bucket dict to share the scoring path below.
        buckets_dict: dict[Any, pl.DataFrame] = {0: bucketed}
        print(
            f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: "
            f"small-block fast path (height={prepared_df.height} < n_buckets={n_buckets}); "
            f"skipping hash+partition_by. See #422.",
            flush=True,
        )
    else:
        _tb = time.perf_counter()
        bucketed = keyed.with_columns(
            (pl.col("__block_key__").hash(seed=BUCKET_HASH_SEED) % n_buckets)
            .alias("__bucket__")
        )
        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: bucketed (hash %% N) in {time.perf_counter()-_tb:.2f}s", flush=True)

        with stage("bucket_partition"):
            _tp = time.perf_counter()
            # First-level partition: N eager DataFrames keyed by bucket id.
            # Polars >= 1.0 returns tuple-keyed dict when as_dict=True with a
            # single partition column; unwrap below.
            buckets_dict = bucketed.partition_by(
                "__bucket__", as_dict=True,
            )
            print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: partition_by(bucket) in {time.perf_counter()-_tp:.2f}s -> {len(buckets_dict)} buckets", flush=True)

    frozen_exclude = frozenset(matched_pairs)
    non_empty_buckets = [b for b in buckets_dict.values() if b.height > 0]
    n_non_empty_buckets = len(non_empty_buckets)
    print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: {n_non_empty_buckets} non-empty buckets ready for scoring", flush=True)

    # Fast-path eligibility: tiny-block workloads (5M-on-one-node, p99 block
    # size ~3 rows) spend most of bucket_score wall in Python orchestration --
    # numpy 3x3 matrix allocations, PluginRegistry lookup, _get_transformed_values
    # dispatch per (block x field). For the simple "weighted matchkey, plain
    # fuzzy scorers, no NE/rerank/exact/record_embedding" shape we can skip
    # find_fuzzy_matches entirely and do per-pair scoring directly. Pre-resolve
    # the scorer callable + xform column per field ONCE at score_buckets entry
    # (instead of per block).
    from goldenmatch.core.scorer import find_fuzzy_matches

    fast_path_specs = _resolve_fast_path(
        mk, prepared_df,
        across_files_only=across_files_only,
        source_lookup=source_lookup,
        target_ids=target_ids,
    )

    # Native fast-path eligibility resolved ONCE: gated on, and every field's
    # scorer implemented by the native kernel. None -> Python per-pair loop.
    native_scorer_ids: list[int] | None = None
    if fast_path_specs is not None and native_enabled("block_scoring"):
        ids = [_NATIVE_SCORER_IDS.get(spec[3]) for spec in fast_path_specs[2]]
        if all(i is not None for i in ids):
            native_scorer_ids = ids  # type: ignore[assignment]

    # Track 1 Fix B: build the native ExcludeSet ONCE here, BEFORE the bucket
    # worker loop. Previously _score_one_bucket_fast called
    # list(frozen_exclude) + passed it positionally, which forced the kernel
    # to materialize a fresh Vec, marshal across PyO3, and rebuild a Rust
    # HashSet ON EVERY worker call (64 at default n_buckets). At 10M with
    # 36.5M exact pairs that was ~1170s of bucket_score wall (verified
    # against QIS 10M-v9 native: bucket_score 1370s, kernel scoring math <50s).
    # Now: one set built once, every worker call passes the Arc handle.
    # Falls back to None (no exclude) when native isn't available or the
    # build_exclude_set kernel isn't in the loaded native module (older wheel).
    native_exclude_handle = None
    if native_scorer_ids is not None:
        try:
            _build = native_module().build_exclude_set
        except AttributeError:
            _build = None
        if _build is not None and frozen_exclude:
            _t_eb = time.perf_counter()
            native_exclude_handle = _build(list(frozen_exclude))
            print(
                f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: "
                f"build_exclude_set({len(frozen_exclude)} pairs) in "
                f"{time.perf_counter()-_t_eb:.2f}s",
                flush=True,
            )

    def _score_one_bucket_fast(bucket_df: pl.DataFrame) -> tuple[list[tuple[int, int, float]], int]:
        # Fast path for tiny-block workloads. Pre-extracts each transformed
        # field as a Python list ONCE per bucket, then iterates pairs within
        # each block via simple Python loops + direct scorer.score_pair calls.
        # Skips numpy NxN matrix dance entirely -- for 3-row blocks the matrix
        # is a 3x3 array and the alloc/free + np.zeros call cost dwarfs the
        # actual rapidfuzz work.
        assert fast_path_specs is not None  # gated by dispatcher
        threshold, total_weight, field_specs = fast_path_specs
        sorted_df = bucket_df.sort("__block_key__")
        sizes = (
            sorted_df.lazy()
            .group_by("__block_key__", maintain_order=True)
            .agg(pl.len().alias("__size__"))
            .collect()
        )
        if sizes.height == 0:
            return [], 0
        size_list = sizes["__size__"].to_list()
        weights = [w for _col, w, _fn, _name in field_specs]

        # Native Arrow kernel: hand the block-sorted __row_id__ + field columns
        # to Rust as zero-copy Arrow buffers, skipping the per-element .to_list()
        # materialization + PyO3 Vec<Vec<Option<String>>> clone that dominate
        # this stage (~58% of native wall at 1M rows -> ~2x kernel speedup; see
        # scripts/bench_native_kernels.py). Identical (min,max) pairs in the same
        # block order as the Vec kernel + the Python loop (parity asserted in
        # tests/test_native_parity.py). __row_id__ is cast to Int64 (no-op when
        # already Int64) because the kernel requires int64 buffers.
        if native_scorer_ids is not None:
            row_ids_arrow = sorted_df["__row_id__"].cast(pl.Int64).to_arrow()
            field_arrays_arrow = [
                sorted_df[col].to_arrow() for col, _w, _fn, _name in field_specs
            ]
            # Track 1 Fix B: prefer the prebuilt exclude handle (closed-over
            # native_exclude_handle from score_buckets entry). The kernel's
            # exclude= and exclude_set= params are mutually opt-in -- when
            # exclude_set is None, the kernel rebuilds a HashSet from the Vec
            # (legacy path); when exclude_set is the Arc handle, kernel uses
            # it directly. Older native builds without build_exclude_set
            # fall through to the legacy positional Vec path.
            if native_exclude_handle is not None:
                pairs = native_module().score_block_pairs_arrow(
                    row_ids_arrow, field_arrays_arrow, size_list,
                    native_scorer_ids, weights, total_weight, threshold,
                    exclude_set=native_exclude_handle,
                )
            else:
                pairs = native_module().score_block_pairs_arrow(
                    row_ids_arrow, field_arrays_arrow, size_list,
                    native_scorer_ids, weights, total_weight, threshold,
                    list(frozen_exclude),
                )
            local_blocks = sum(1 for s in size_list if s >= 2)
            return pairs, local_blocks

        # Python per-pair fallback: materialize the columns as lists.
        # field_specs: list of (xform_col, weight, score_fn, scorer_name).
        row_ids = sorted_df["__row_id__"].to_list()
        field_arrays = [
            sorted_df[col].to_list() for col, _w, _fn, _name in field_specs
        ]
        score_fns = [fn for _col, _w, fn, _name in field_specs]
        n_fields = len(field_specs)
        local_pairs: list[tuple[int, int, float]] = []
        local_blocks = 0
        offset = 0
        for size in size_list:
            if size >= 2:
                end = offset + size
                for i in range(offset, end - 1):
                    ri = row_ids[i]
                    for j in range(i + 1, end):
                        rj = row_ids[j]
                        if ri < rj:
                            pair_key = (ri, rj)
                        else:
                            pair_key = (rj, ri)
                        if pair_key in frozen_exclude:
                            continue
                        score_sum = 0.0
                        weight_sum = 0.0
                        for f_idx in range(n_fields):
                            va = field_arrays[f_idx][i]
                            vb = field_arrays[f_idx][j]
                            if va is None or vb is None:
                                continue
                            s = score_fns[f_idx](va, vb)
                            if s is None:
                                continue
                            score_sum += s * weights[f_idx]
                            weight_sum += weights[f_idx]
                        if weight_sum > 0:
                            combined = score_sum / total_weight
                            if combined >= threshold:
                                local_pairs.append(
                                    (pair_key[0], pair_key[1], float(combined))
                                )
                local_blocks += 1
            offset += size
        return local_pairs, local_blocks

    def _score_one_bucket(bucket_df: pl.DataFrame) -> tuple[list[tuple[int, int, float]], int]:
        # Sort once, slice per block (zero-copy view over the sorted parent).
        # Avoids partition_by's millions-of-tiny-eager-frames allocation that
        # fragments glibc's malloc arena on Linux (1.4 GB / 30s RSS climb).
        sorted_df = bucket_df.sort("__block_key__")
        sizes = (
            sorted_df.lazy()
            .group_by("__block_key__", maintain_order=True)
            .agg(pl.len().alias("__size__"))
            .collect()
        )
        if sizes.height == 0:
            return [], 0
        # Pre-materialize as Python lists so the inner loop avoids per-iter
        # Polars scalar indexing (the hottest line per block at 1.67M blocks).
        size_list = sizes["__size__"].to_list()
        local_pairs: list[tuple[int, int, float]] = []
        local_blocks = 0
        offset = 0
        for size in size_list:
            if size >= 2:
                block_df = sorted_df.slice(offset, size)
                if across_files_only and source_lookup:
                    sources_in_block = block_df["__source__"].unique().to_list()
                    if len(sources_in_block) < 2:
                        offset += size
                        continue
                pairs = find_fuzzy_matches(
                    block_df, mk,
                    exclude_pairs=frozen_exclude,
                    pre_scored_pairs=None,
                )
                if across_files_only and source_lookup:
                    pairs = [
                        (a, b, s) for a, b, s in pairs
                        if source_lookup.get(a) != source_lookup.get(b)
                    ]
                if target_ids is not None:
                    pairs = [
                        (a, b, s) for a, b, s in pairs
                        if (a in target_ids) != (b in target_ids)
                    ]
                local_pairs.extend(pairs)
                local_blocks += 1
            offset += size
        return local_pairs, local_blocks

    all_pairs: list[tuple[int, int, float]] = []
    total_blocks_scored = 0

    with stage("bucket_score"):
        # rapidfuzz.cdist releases the GIL inside the scorer, so threads
        # give real parallelism. Mirror score_blocks_parallel's worker cap.
        max_workers = min(n_non_empty_buckets, os.cpu_count() or 4)
        _ts = time.perf_counter()
        worker = _score_one_bucket_fast if fast_path_specs is not None else _score_one_bucket
        path_label = "fast" if fast_path_specs is not None else "find_fuzzy_matches"
        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: starting bucket_score with max_workers={max_workers} path={path_label}", flush=True)
        if max_workers <= 1 or n_non_empty_buckets <= 2:
            for bucket_df in non_empty_buckets:
                pairs, n = worker(bucket_df)
                all_pairs.extend(pairs)
                total_blocks_scored += n
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for pairs, n in pool.map(worker, non_empty_buckets):
                    all_pairs.extend(pairs)
                    total_blocks_scored += n
        for a, b, _s in all_pairs:
            matched_pairs.add((min(a, b), max(a, b)))

    record_metrics({
        "bucket_count": n_non_empty_buckets,
        "bucket_n_target": n_buckets,
        "block_count_scored": total_blocks_scored,
    })
    print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: bucket_score done in {time.perf_counter()-_ts:.2f}s, {total_blocks_scored} blocks, {len(all_pairs)} pairs", flush=True)
    logger.info(
        "score_buckets: %d non-empty buckets (target N=%d), %d blocks scored, %d pairs",
        n_non_empty_buckets, n_buckets, total_blocks_scored, len(all_pairs),
    )
    return all_pairs
