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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import polars as pl

from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig, MatchkeyConfig
from goldenmatch.core._native_loader import native_enabled, native_module
from goldenmatch.core.bench import record_metrics, stage
from goldenmatch.core.blocker import _build_block_key_expr

logger = logging.getLogger(__name__)

# One-time guard so the stale-native-wheel warning (issue #688) fires at most
# once per process instead of once per score_buckets call.
_WARNED_STALE_NATIVE_WHEEL = False


def _warn_stale_native_wheel_once(n_exclude: int) -> None:
    """Warn (once) when the loaded native wheel predates build_exclude_set.

    The published ``goldenmatch-native 0.1.0`` wheel (2026-05-27) shipped one
    day before ``build_exclude_set`` / ``ExcludeSet`` landed (#552, 2026-05-28),
    so any env that pip-installs it instead of building in-tree hits the legacy
    exclude path. Surface the skew instead of silently degrading -- this was the
    root cause of issue #688's 44x bucket_score slowdown.
    """
    global _WARNED_STALE_NATIVE_WHEEL
    if _WARNED_STALE_NATIVE_WHEEL:
        return
    _WARNED_STALE_NATIVE_WHEEL = True
    logger.warning(
        "goldenmatch-native is loaded but lacks build_exclude_set (pre-#552 "
        "wheel; the published goldenmatch-native 0.1.0 is such a wheel). The "
        "block scorer is using its exclude-set fallback (empty exclude + Python "
        "post-filter over %d excluded pairs) -- still fast, but upgrading "
        "goldenmatch-native or rebuilding in-tree (scripts/build_native.py) "
        "restores the native Arc-handle path. See issue #688.",
        n_exclude,
    )


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
        # DECLINE the fast path for ensemble (return None -> find_fuzzy_matches
        # slow path). A prior per-pair reimplementation (max(jw, ts, sx*0.8))
        # claimed matrix-path equivalence but measurably diverged: on Febrl3
        # auto-config (3 ensemble fields) it dropped recall 0.922 -> 0.782 vs
        # polars-direct (F1 0.9332 -> 0.8768), with bucket near-perfect precision
        # -- i.e. the reimplementation scored STRICTER than the matrix ensemble,
        # pushing true pairs below threshold. Declining restores parity
        # (recall 0.9221, F1 0.9326). The matrix ensemble (core/scorer.py) is the
        # single source of truth; do NOT reintroduce a per-pair ensemble without
        # a field-level parity test against find_fuzzy_matches. Native already
        # declines ensemble (_NATIVE_SCORER_IDS covers only the 4 plain scorers),
        # and plain-scorer configs (the 5M/25M scale path) keep the fast path.
        return None
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


# Scorers that score_field() handles directly (without raising). NE entries
# whose scorer is NOT in this set get silently skipped at runtime via the
# _NE_BROKEN cache in core/scorer.py::_apply_negative_evidence -- so they
# contribute zero penalty to the final score. The fast path can safely run
# when every NE scorer is one of these "will-fail" names, because the
# computed score matches what the slow path would produce (penalty=0 either
# way). Without this check, auto-config's promote_negative_evidence on
# 'ensemble' / 'embedding' / 'record_embedding' (none of which score_field
# implements) forces the entire workload onto the slow path even though the
# NE entries don't actually do anything at runtime.
_SCORE_FIELD_DIRECT_SCORERS: frozenset[str] = frozenset({
    "exact", "jaro_winkler", "levenshtein", "token_sort",
    "soundex_match", "dice", "jaccard",
})


def _ne_effectively_empty(mk: MatchkeyConfig) -> bool:
    """True when matchkey.negative_evidence is empty OR every NE entry uses
    a scorer name that score_field doesn't handle.

    Historical role (pre-2026-05-29): this gated fast-path eligibility --
    NE with callable scorers forced the slow path. Now the fast path engages
    with NE math inline (`_resolve_ne_specs` + per-pair penalty in
    `_score_one_bucket_fast`). The helper survives because:
      1. The slow path's `_NE_BROKEN` cache still uses the same classification
         to silently skip broken NE entries.
      2. Tests in test_score_buckets_fast_path_gate.py assert this classification
         independently of whether the fast path declines or engages.
      3. Controller / planner policy decisions can still consult it to detect
         "NE no-op" workloads.
    """
    ne = getattr(mk, "negative_evidence", None)
    if not ne:
        return True
    for ne_entry in ne:
        scorer = getattr(ne_entry, "scorer", None)
        if scorer is None or scorer in _SCORE_FIELD_DIRECT_SCORERS:
            return False
    return True


# NE spec layout: (xform_col, score_pair_fn, threshold, penalty). One per
# resolvable NE entry. Empty list means "no NE math needed" -- either NE
# was empty or every NE entry's scorer is in _NE_BROKEN territory (matches
# the slow path's silent-skip behavior).
NeSpec = tuple[str, Any, float, float]


def _resolve_ne_specs(
    mk: MatchkeyConfig,
    prepared_df: pl.DataFrame,
) -> list[NeSpec]:
    """Resolve mk.negative_evidence into per-pair callable specs.

    Mirrors the slow path's `_apply_negative_evidence`:
      - NE entries whose scorer isn't in `_SCORE_FIELD_DIRECT_SCORERS`
        are silently skipped (the slow path's _NE_BROKEN cache does this
        at runtime; we replicate the policy at gate-time).
      - Entries whose xform column isn't precomputed are skipped (same
        rationale -- caller can't access transformed values without it).
      - Penalty math is `final = max(0, score_positive - sum(penalties))`
        applied where the slow path uses the same formula.
    """
    from goldenmatch.core.matchkey import _xform_sig

    out: list[NeSpec] = []
    ne_list = getattr(mk, "negative_evidence", None) or []
    for ne in ne_list:
        scorer = getattr(ne, "scorer", None)
        if scorer is None or scorer not in _SCORE_FIELD_DIRECT_SCORERS:
            # Mirror slow-path _NE_BROKEN behavior: contribute zero penalty.
            continue
        fn = _resolve_score_pair_callable(scorer)
        if fn is None:
            continue
        xform_col = _xform_sig(ne)
        if xform_col not in prepared_df.columns:
            continue
        out.append((xform_col, fn, float(ne.threshold), float(ne.penalty)))
    return out


def _resolve_fast_path(
    mk: MatchkeyConfig,
    prepared_df: pl.DataFrame,
    *,
    across_files_only: bool,
    source_lookup: dict[int, str] | None,
    target_ids: set[int] | None,
) -> tuple[float, float, list[tuple[str, float, Any, str]], list[NeSpec]] | None:
    """Decide whether mk is fast-path eligible and pre-resolve field specs.

    Returns (threshold, total_weight, field_specs, ne_specs) when eligible,
    else None. Resolution is done ONCE at score_buckets entry so per-pair
    work never touches the PluginRegistry, _get_transformed_values, or
    scorer-name dispatch.

    field_specs: list of (xform_col, weight, score_pair_fn, scorer_name).
    ne_specs:    list of (xform_col, score_pair_fn, threshold, penalty);
                 empty when NE is missing or all-broken (matches today's
                 _ne_effectively_empty behavior). Non-empty when NE has
                 resolvable scorer entries that contribute real penalty
                 (new in 2026-05-29 -- previously declined).

    Eligibility gates (conservative — fall back to find_fuzzy_matches for
    anything more complex):
      - mk.type == "weighted"
      - mk.threshold set
      - no rerank / LLM
      - every field resolves to a score_pair callable via
        _resolve_score_pair_callable AND has its xform column precomputed
      - NE entries with resolvable scorers WERE a decline gate; now they
        engage the fast path with per-pair penalty math.
    """
    from goldenmatch.core.matchkey import _xform_sig

    # Diagnostic: log which gate declines eligibility so workloads stuck on
    # the slow find_fuzzy_matches path can be debugged without rebuilding.
    # Print once per call (i.e. per matchkey resolution), not per pair.
    def _decline(reason: str) -> None:
        print(f"[score_buckets._resolve_fast_path] declined: {reason}", flush=True)

    if mk.type != "weighted":
        _decline(f"mk.type={mk.type!r} (need 'weighted')")
        return None
    if mk.threshold is None:
        _decline("mk.threshold is None")
        return None
    if getattr(mk, "rerank", False):
        _decline("mk.rerank=True (auto-config enables for 3+ field weighted matchkeys)")
        return None
    if getattr(mk, "llm", None):
        _decline("mk.llm is set")
        return None
    # NOTE: match-mode (across_files_only / source_lookup / target_ids) USED
    # to decline the fast path here. That was conservative -- the fast path
    # can engage with these set because they only act as post-filters on
    # emitted pairs, not as scoring math. The actual filtering happens after
    # the worker emits candidate pairs (mirrors _score_one_bucket's behavior).
    # Removed in PR #572 (match-mode widening); NE penalty math composes
    # cleanly on top because NE is per-pair scoring and match-mode is
    # per-pair post-filter -- they don't interact.
    if not mk.fields:
        _decline("mk.fields is empty")
        return None

    field_specs: list[tuple[str, float, Any, str]] = []
    total_weight = 0.0
    for f in mk.fields:
        scorer = getattr(f, "scorer", None)
        weight = getattr(f, "weight", None)
        if scorer is None or weight is None:
            _decline(f"field has scorer={scorer!r} weight={weight!r}")
            return None
        fn = _resolve_score_pair_callable(scorer)
        if fn is None:
            _decline(f"_resolve_score_pair_callable({scorer!r}) is None")
            return None
        xform_col = _xform_sig(f)
        if xform_col not in prepared_df.columns:
            return None
        field_specs.append((xform_col, float(weight), fn, scorer))
        total_weight += float(weight)
    if total_weight <= 0:
        _decline(f"total_weight={total_weight}")
        return None
    ne_specs = _resolve_ne_specs(mk, prepared_df)
    # Diagnostic on the success path: log matchkey shape so we can compare
    # what the controller commits at different row counts (rerank thresholds
    # and NE promotion are scale-dependent).
    scorer_names = [s for _, _, _, s in field_specs]
    ne_scorers = [getattr(e, "scorer", "?") for e in (mk.negative_evidence or [])]
    print(
        f"[score_buckets._resolve_fast_path] ENGAGED: "
        f"n_fields={len(mk.fields)} scorers={scorer_names} "
        f"threshold={mk.threshold} rerank={getattr(mk, 'rerank', False)} "
        f"ne_scorers={ne_scorers} ne_resolved={len(ne_specs)}",
        flush=True,
    )
    return (float(mk.threshold), total_weight, field_specs, ne_specs)


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
        blocking_config: Source for the block-key expression. Iterates
            ``blocking_config.passes or blocking_config.keys`` (multi-pass
            blocking), accumulating pairs across every pass. Like
            polars-direct, the exclude set is frozen ONCE across all passes
            and cross-pass duplicate pairs ARE emitted; they collapse
            downstream in build_clusters' pair_scores dict. This is exact
            parity with polars-direct by construction. Note the DELIBERATE
            difference from polars-direct: polars-direct dedups identical
            block keys ACROSS passes (``blocker.py::_build_multi_pass_blocks``
            via its ``seen_keys`` set), whereas this bucket path re-scores each
            pass independently and emits cross-pass DUPLICATE PAIRS that
            collapse in build_clusters' ``pair_scores`` dict. Consequence:
            ``block_count_scored`` / ``bucket_count`` metrics read HIGHER for
            bucket than for polars on overlapping-key multi-pass configs --
            expected, not a bug. Do NOT "fix" this by adding block-key dedup;
            the duplicate-pair collapse is the parity mechanism.
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

    # Multi-pass blocking: iterate every pass and accumulate pairs. `passes`
    # is None for static / single-key configs, so fall back to `keys`.
    pass_keys = blocking_config.passes or blocking_config.keys

    # Oversized-block skip (parity with polars-direct's build_blocks in
    # core/blocker.py). Read once and close over in the nested scoring
    # workers. A block is "oversized" when skip_oversized and size >
    # max_block_size; such blocks are skipped entirely (no pairs emitted),
    # matching polars' behavior when its _auto_split_block can't recover.
    skip_oversized = blocking_config.skip_oversized
    max_block_size = blocking_config.max_block_size

    if n_buckets is None:
        n_buckets = _default_n_buckets()

    # Diag prints (flushed) so we can see substep timing on runner heartbeats
    # independent of the bench stage recorder, which only logs CLOSED stages.
    # Three 5M Linux runs hung mid-score_buckets with no substage closing;
    # these prints expose the actual hang line.
    _t0 = time.perf_counter()
    print(f"[score_buckets] entry: prepared_df.height={prepared_df.height} n_buckets={n_buckets}", flush=True)

    # Verbose per-bucket timing breakdown (issue #688 diagnosis aid). OFF by
    # default; set GOLDENMATCH_BUCKET_DEBUG=1 to split every native bucket call
    # into prep (sort + group_by + to_arrow) vs kernel (score_block_pairs_arrow)
    # vs post-filter, accumulated across all buckets and printed once at the end.
    # This is the split that localizes "Polars wrapping vs the Rust kernel" --
    # e.g. it shows the kernel call dominating when rayon parks on a futex
    # (issue #688). Zero cost when off (one env read + a couple of branches).
    _bucket_debug = os.environ.get("GOLDENMATCH_BUCKET_DEBUG", "0") not in (
        "0", "", "false", "False", "no", "off",
    )
    _dbg_lock = threading.Lock()
    # rows: (prep_s, kernel_s, postfilter_s, n_blocks, n_pairs_emitted)
    _dbg_rows: list[tuple[float, float, float, int, int]] = []

    # Slim projection: drop columns no score-worker reads. The audit
    # (2026-05-29) showed every reader in this module touches only
    # __row_id__ / __source__ / __block_key__ / __xform_*__ plus the
    # raw source fields named by the blocking key. Everything else in
    # prepared_df is dead weight from bucket_assign onward.
    #
    # v30 QIS 10M bench (2026-05-29): peak RSS dropped 39.3 GB -> 35.5 GB
    # (-3.8 GB, -9.7%) at F1=0.9886 invariant and wall flat. The savings
    # come from downstream stages (partition_by, bucket_score, cluster,
    # golden) holding a smaller per-bucket frame -- NOT from the .select()
    # being zero-copy as initially hypothesized (Polars allocates ~10 GB
    # to consolidate __xform_*__ chunks during select). Default ON;
    # opt out via GOLDENMATCH_BUCKET_SLIM_PROJECTION=0 if a workload
    # downstream of score_buckets ever needs a column we drop.
    if os.environ.get("GOLDENMATCH_BUCKET_SLIM_PROJECTION", "1") != "0":
        with stage("bucket_slim_projection"):
            keep: list[str] = ["__row_id__"]
            if "__source__" in prepared_df.columns:
                keep.append("__source__")
            keep.extend(c for c in prepared_df.columns if c.startswith("__xform_"))
            # Source fields the block-key expression reads. Multi-key blocking
            # (rare today) accumulates fields across every key in the config.
            block_key_sources: set[str] = set()
            for key in pass_keys:
                block_key_sources.update(key.fields)
            for col in block_key_sources:
                if col in prepared_df.columns and col not in keep:
                    keep.append(col)
            slim_df = prepared_df.select(keep)
            print(
                f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: slim projection "
                f"{len(prepared_df.columns)} -> {len(keep)} cols",
                flush=True,
            )
    else:
        slim_df = prepared_df

    # Freeze the exclude set ONCE across ALL passes (parity with polars-direct,
    # which freezes its exclude snapshot once and emits cross-pass duplicate
    # pairs that collapse downstream in build_clusters' pair_scores dict). We
    # must NOT rebuild this per pass or add an intra-loop matched_pairs skip --
    # that would diverge from polars. frozen_exclude shadows matched_pairs as a
    # Python frozenset -- at 10M-bucket-realistic this is the dominant
    # Python-side accumulator.
    frozen_exclude = frozenset(matched_pairs)

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
    # When NE has resolvable entries, force the Python path -- the native
    # kernel emits pairs filtered against `threshold` BEFORE NE penalty is
    # applied, so we'd have to re-emit + re-threshold downstream. The Python
    # path handles NE math inline at the same per-pair cost. Returning to
    # native-with-NE would mean teaching the kernel to emit pre-penalty
    # candidate pairs (~2x emit volume) -- not worth it until measurement
    # demands it.
    native_scorer_ids: list[int] | None = None
    if fast_path_specs is not None and native_enabled("block_scoring"):
        _, _, _field_specs, _ne_specs = fast_path_specs
        if not _ne_specs:
            ids = [_NATIVE_SCORER_IDS.get(spec[3]) for spec in _field_specs]
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
        elif _build is None and frozen_exclude:
            # Stale/old native wheel: no Arc-handle path available. The worker
            # falls back to empty-exclude + Python post-filter (see below).
            _warn_stale_native_wheel_once(len(frozen_exclude))

    def _apply_match_mode_filter(
        pairs: list[tuple[int, int, float]],
    ) -> list[tuple[int, int, float]]:
        """Mirror the slow path's match-mode post-filter (_score_one_bucket
        lines 544-553). Applies in two stages: across_files_only drops
        same-source pairs; target_ids drops same-side-of-target pairs.

        Both filters are O(pairs) and very cheap relative to scoring; safe
        to apply unconditionally on the fast path now that the gate is gone."""
        if across_files_only and source_lookup is not None:
            pairs = [
                (a, b, s) for a, b, s in pairs
                if source_lookup.get(a) != source_lookup.get(b)
            ]
        if target_ids is not None:
            pairs = [
                (a, b, s) for a, b, s in pairs
                if (a in target_ids) != (b in target_ids)
            ]
        return pairs

    def _score_one_bucket_fast(bucket_df: pl.DataFrame) -> tuple[list[tuple[int, int, float]], int]:
        # Fast path for tiny-block workloads. Pre-extracts each transformed
        # field as a Python list ONCE per bucket, then iterates pairs within
        # each block via simple Python loops + direct scorer.score_pair calls.
        # Skips numpy NxN matrix dance entirely -- for 3-row blocks the matrix
        # is a 3x3 array and the alloc/free + np.zeros call cost dwarfs the
        # actual rapidfuzz work.
        assert fast_path_specs is not None  # gated by dispatcher
        threshold, total_weight, field_specs, ne_specs = fast_path_specs
        _te = time.perf_counter() if _bucket_debug else 0.0
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
            # Oversized-block skip on the native path: filter BOTH the per-row
            # arrow arrays AND the size_list BEFORE handing them to the kernel.
            # The kernel walks sorted_df rows block-contiguously (it's sorted by
            # __block_key__), so a per-row mask built by repeating each block's
            # keep-flag `size` times stays aligned to the rows. keep also folds
            # in the size<2 no-op (those blocks emit no pairs anyway, but
            # dropping them keeps kept_size_list and the arrays consistent).
            # See _score_one_bucket for the polars-direct parity rationale +
            # auto-split follow-up note.
            keep = [
                (s >= 2) and not (skip_oversized and s > max_block_size)
                for s in size_list
            ]
            native_sorted_df = sorted_df
            kept_size_list = size_list
            if not all(keep):
                import numpy as np

                row_mask = np.repeat(np.array(keep, dtype=bool), size_list)
                native_sorted_df = sorted_df.filter(pl.Series(row_mask))
                kept_size_list = [s for s, k in zip(size_list, keep) if k]
                if not kept_size_list:
                    return [], 0
            row_ids_arrow = native_sorted_df["__row_id__"].cast(pl.Int64).to_arrow()
            field_arrays_arrow = [
                native_sorted_df[col].to_arrow()
                for col, _w, _fn, _name in field_specs
            ]
            size_list = kept_size_list
            _tk0 = time.perf_counter() if _bucket_debug else 0.0
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
                # Legacy/stale native wheel (pre-#552: no build_exclude_set, so
                # native_exclude_handle is None). Passing the full exclude as a
                # fresh Vec on EVERY bucket call makes the kernel rebuild a
                # HashSet per call -- O(buckets * |exclude|), the #552 pathology
                # and the root cause of issue #688's 44x slowdown on the
                # published goldenmatch-native 0.1.0 wheel. Pass an EMPTY exclude
                # and drop excluded pairs in Python after emit instead: the
                # kernel emits only >= threshold pairs (few), so the post-filter
                # is O(emitted), and the wasted scoring of excluded intra-block
                # pairs is cheap rapidfuzz-rs work. The emitted ids are canonical
                # (min, max) (kernel pair_key), matching frozen_exclude's keying,
                # so the output pair set is identical to the handle path: a pair
                # in frozen_exclude that scores >= threshold is emitted then
                # removed here; one that scores < threshold is dropped either way.
                pairs = native_module().score_block_pairs_arrow(
                    row_ids_arrow, field_arrays_arrow, size_list,
                    native_scorer_ids, weights, total_weight, threshold,
                    [],
                )
                if frozen_exclude:
                    pairs = [
                        p for p in pairs if (p[0], p[1]) not in frozen_exclude
                    ]
            _tk1 = time.perf_counter() if _bucket_debug else 0.0
            local_blocks = sum(1 for s in size_list if s >= 2)
            # Match-mode post-filter (native path doesn't know about
            # source_lookup or target_ids; apply in Python after emit).
            if across_files_only or target_ids is not None:
                pairs = _apply_match_mode_filter(pairs)
            if _bucket_debug:
                _tk2 = time.perf_counter()
                with _dbg_lock:
                    _dbg_rows.append(
                        (_tk0 - _te, _tk1 - _tk0, _tk2 - _tk1, local_blocks, len(pairs))
                    )
            return pairs, local_blocks

        # Python per-pair fallback: materialize the columns as lists.
        # field_specs: list of (xform_col, weight, score_fn, scorer_name).
        row_ids = sorted_df["__row_id__"].to_list()
        field_arrays = [
            sorted_df[col].to_list() for col, _w, _fn, _name in field_specs
        ]
        score_fns = [fn for _col, _w, fn, _name in field_specs]
        n_fields = len(field_specs)
        # NE per-pair specs (post-2026-05-29 widening). Pre-materialize the
        # NE xform columns; empty when NE missing / all-broken (no overhead).
        ne_arrays = [sorted_df[col].to_list() for col, _fn, _t, _p in ne_specs]
        ne_fns = [fn for _col, fn, _t, _p in ne_specs]
        ne_thresholds = [t for _col, _fn, t, _p in ne_specs]
        ne_penalties = [p for _col, _fn, _t, p in ne_specs]
        n_ne = len(ne_specs)
        local_pairs: list[tuple[int, int, float]] = []
        local_blocks = 0
        offset = 0
        for size in size_list:
            if size >= 2:
                # Skip oversized blocks (see _score_one_bucket for rationale /
                # polars-direct parity + auto-split follow-up note).
                if skip_oversized and size > max_block_size:
                    offset += size
                    continue
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
                        if weight_sum <= 0:
                            continue
                        combined = score_sum / total_weight
                        # NE penalty math (mirrors core/scorer.py
                        # _apply_negative_evidence): subtract penalty when an
                        # NE field's similarity is below its threshold. Clamp
                        # at 0. Same formula as the slow path.
                        if n_ne > 0:
                            penalty = 0.0
                            for k in range(n_ne):
                                na = ne_arrays[k][i]
                                nb = ne_arrays[k][j]
                                if na is None or nb is None:
                                    continue
                                sim = ne_fns[k](na, nb)
                                if sim is None:
                                    continue
                                if sim < ne_thresholds[k]:
                                    penalty += ne_penalties[k]
                            if penalty > 0:
                                combined = max(0.0, combined - penalty)
                        if combined >= threshold:
                            local_pairs.append(
                                (pair_key[0], pair_key[1], float(combined))
                            )
                local_blocks += 1
            offset += size
        if across_files_only or target_ids is not None:
            local_pairs = _apply_match_mode_filter(local_pairs)
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
                # Skip oversized blocks, matching polars-direct's build_blocks
                # (core/blocker.py): when skip_oversized is set and a block
                # exceeds max_block_size, polars skips it (the common case when
                # _auto_split_block can't recover). FOLLOW-UP: replicate polars'
                # auto-split recovery here for parity on splittable hot blocks;
                # out of scope for this fix.
                if skip_oversized and size > max_block_size:
                    offset += size
                    continue
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

    def _score_single_pass(
        key: BlockingKeyConfig,
    ) -> tuple[list[tuple[int, int, float]], int, int]:
        """Key, bucket, partition, and score one blocking pass.

        Returns (pass_pairs, blocks_scored, n_non_empty_buckets). Builds its
        own keyed/bucketed frames off the immutable slim_df and `del`s them at
        partition time so only one pass is resident at a time (preserves peak
        RSS). Accumulates into a LOCAL pass_pairs -- it must NOT mutate
        matched_pairs (that happens once, after all passes, in the caller).
        """
        key_expr = _build_block_key_expr(key)

        with stage("bucket_assign"):
            _ta = time.perf_counter()
            keyed = slim_df.with_columns(key_expr)
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
            # Adds an i64 __bucket__ column at 10M rows -- ~80 MB of int64 plus
            # whatever Polars holds for the hash intermediate. Wrap so the RSS
            # bench can attribute it instead of pooling it into the unwrapped
            # gap between bucket_assign and bucket_partition.
            with stage("bucket_hash_modulo"):
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
                # Free the pre-partition `keyed` and `bucketed` parents.
                # partition_by built N independent eager frames; the original
                # contiguous parents are dead weight from this point forward.
                del keyed
                del bucketed
                print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: partition_by(bucket) in {time.perf_counter()-_tp:.2f}s -> {len(buckets_dict)} buckets", flush=True)

        with stage("bucket_post_partition_setup"):
            non_empty_buckets = [b for b in buckets_dict.values() if b.height > 0]
            n_non_empty_buckets = len(non_empty_buckets)
        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: {n_non_empty_buckets} non-empty buckets ready for scoring", flush=True)

        pass_pairs: list[tuple[int, int, float]] = []
        pass_blocks_scored = 0
        if n_non_empty_buckets == 0:
            return pass_pairs, pass_blocks_scored, n_non_empty_buckets

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
                    pass_pairs.extend(pairs)
                    pass_blocks_scored += n
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    for pairs, n in pool.map(worker, non_empty_buckets):
                        pass_pairs.extend(pairs)
                        pass_blocks_scored += n
        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: bucket_score done in {time.perf_counter()-_ts:.2f}s, {pass_blocks_scored} blocks, {len(pass_pairs)} pairs", flush=True)
        return pass_pairs, pass_blocks_scored, n_non_empty_buckets

    all_pairs: list[tuple[int, int, float]] = []
    total_blocks_scored = 0
    total_non_empty = 0
    slim_cols = set(slim_df.columns)
    for key in pass_keys:
        if not set(key.fields) <= slim_cols:
            logger.warning(
                "score_buckets: skipping pass %s -- field(s) %s absent from prepared_df",
                key.fields, sorted(set(key.fields) - slim_cols),
            )
            continue
        pass_pairs, blocks_scored, n_non_empty = _score_single_pass(key)
        all_pairs.extend(pass_pairs)
        total_blocks_scored += blocks_scored
        total_non_empty += n_non_empty
    for a, b, _s in all_pairs:
        matched_pairs.add((min(a, b), max(a, b)))

    record_metrics({
        "bucket_count": total_non_empty,
        "bucket_n_target": n_buckets,
        "block_count_scored": total_blocks_scored,
    })
    logger.info(
        "score_buckets: %d non-empty buckets (target N=%d), %d blocks scored, %d pairs",
        total_non_empty, n_buckets, total_blocks_scored, len(all_pairs),
    )
    if _bucket_debug and _dbg_rows:
        n_calls = len(_dbg_rows)
        prep_s = sum(r[0] for r in _dbg_rows)
        kern_s = sum(r[1] for r in _dbg_rows)
        post_s = sum(r[2] for r in _dbg_rows)
        n_blocks = sum(r[3] for r in _dbg_rows)
        n_pairs = sum(r[4] for r in _dbg_rows)
        tot_s = prep_s + kern_s + post_s
        slowest = max(_dbg_rows, key=lambda r: r[1]) if _dbg_rows else (0, 0, 0, 0, 0)

        def _pct(x: float) -> float:
            return (100.0 * x / tot_s) if tot_s > 0 else 0.0

        print(
            "[score_buckets][DEBUG] native bucket-call breakdown over "
            f"{n_calls} calls / {n_blocks} blocks / {n_pairs} pairs "
            f"(set GOLDENMATCH_BUCKET_DEBUG=0 to silence):\n"
            f"  prep   (sort+group_by+to_arrow): {prep_s:7.3f}s ({_pct(prep_s):5.1f}%)\n"
            f"  kernel (score_block_pairs_arrow): {kern_s:7.3f}s ({_pct(kern_s):5.1f}%)\n"
            f"  post   (match-mode filter):       {post_s:7.3f}s ({_pct(post_s):5.1f}%)\n"
            f"  total in-worker: {tot_s:.3f}s; slowest single kernel call: {slowest[1]:.3f}s",
            flush=True,
        )
    return all_pairs
