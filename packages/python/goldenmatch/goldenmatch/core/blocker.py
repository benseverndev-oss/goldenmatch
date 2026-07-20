"""Blocker for GoldenMatch — groups records into blocks for comparison."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, cast

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.complexity_profile import BlockingProfile
from goldenmatch.core.profile_emitter import _emitter_stack, current_emitter
from goldenmatch.utils.transforms import apply_transforms

logger = logging.getLogger(__name__)


def _percentile(xs: list[int], q: float) -> int:
    """Return the q-th percentile of a sorted list of ints."""
    if not xs:
        return 0
    idx = max(0, min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1))
    return xs[idx]


def _emit_blocking_profile(
    blocks: list[BlockResult],
    config: BlockingConfig,
    lf: pl.LazyFrame,
) -> None:
    """Compute and emit a BlockingProfile to the active emitter.

    Short-circuits immediately when no capture is active so non-controller
    pipeline runs pay zero cost beyond the ``_emitter_stack.get()`` call.
    """
    if not _emitter_stack.get():
        return

    # n_rows is computed here, only when an emitter is active. Frame-type
    # agnostic + polars-free-capable: the arrow seam Frame exposes ``height()``
    # (num_rows, no polars import), so the emitter no longer forces an
    # arrow->polars round trip just to count rows. A polars LazyFrame (the
    # learned/canopy/ann strategies still pass one) has no ``height`` and needs
    # a collect -- polars is available on that path.
    _height = getattr(lf, "height", None)
    if _height is None:
        # polars LazyFrame has no ``height``; collect the count (polars present here)
        n_rows = int(lf.select(pl.len()).collect().item())
    else:
        # arrow-seam Frame exposes num_rows via ``height`` (property) or ``height()``
        # (method) -- no polars import. cast narrows the object-typed value for int().
        _hv = _height() if callable(_height) else _height
        n_rows = int(cast(int, _hv))

    # Determine keys_used: prefer passes if truthy, else keys if truthy, else []
    if config.passes:
        keys_used = [list(k.fields) for k in config.passes]
    elif config.keys:
        keys_used = [list(k.fields) for k in config.keys]
    else:
        keys_used = []

    # Collect block sizes by collecting each LazyFrame.
    # For static/adaptive blocks the underlying DataFrame is already in memory
    # (group_df.lazy()); collecting again is O(1) copy.
    sizes: list[int] = []
    for b in blocks:
        try:
            size = b.n_rows()
        except Exception:
            size = 0
        sizes.append(size)

    sizes_sorted = sorted(sizes)
    n_blocks = len(sizes_sorted)

    total_comparisons = sum(s * (s - 1) // 2 for s in sizes_sorted)
    max_pairs = n_rows * (n_rows - 1) // 2
    reduction_ratio = 1.0 - total_comparisons / max(1, max_pairs)

    singleton_block_count = sum(1 for s in sizes_sorted if s == 1)
    oversized_block_count = sum(1 for s in sizes_sorted if s > config.max_block_size)

    profile = BlockingProfile(
        keys_used=keys_used,
        n_blocks=n_blocks,
        total_comparisons=total_comparisons,
        reduction_ratio=reduction_ratio,
        block_sizes_p50=_percentile(sizes_sorted, 0.50),
        block_sizes_p95=_percentile(sizes_sorted, 0.95),
        block_sizes_p99=_percentile(sizes_sorted, 0.99),
        block_sizes_max=max(sizes_sorted) if sizes_sorted else 0,
        singleton_block_count=singleton_block_count,
        oversized_block_count=oversized_block_count,
    )
    current_emitter().set_blocking(profile)


def _fast_static_block_sizes(
    lf: Any, config: Any  # Frame (seam) or pl.LazyFrame -- PR-6b dual-rep
) -> tuple[list[int], int, int] | None:
    """Vectorized block-size distribution for the ``static`` strategy.

    Stage-D speed lever (spec 2026-06-22): ``measure_blocking_profile`` only
    needs the block-SIZE distribution, but the general path builds every block
    via ``build_blocks`` (materializing one DataFrame per block) then re-collects
    each one — O(n_blocks) Polars round-trips that dominate the wall (measured
    329 ms vs ~9 ms at 1M rows / fixed-cardinality blocking). When the config is
    plain ``static`` blocking, the same sizes fall out of a single per-key
    ``group_by(...).agg(pl.len())`` with no per-block materialization.

    Returns ``None`` — caller falls back to the exact ``build_blocks`` loop —
    whenever the vectorized result would NOT be byte-identical to
    ``_build_static_blocks``: a non-static strategy, or the presence of ANY
    oversized block (``_build_static_blocks`` sub-splits it under both
    ``skip_oversized`` values now -- ANN/auto-split/skip when True, zero-config
    auto-split when False+splittable, #372 -- so the raw group-by sizes diverge).
    Singletons (size < 2) are dropped to mirror the ``if size < 2: continue``
    skip in ``_build_static_blocks``.
    """
    if getattr(config, "strategy", "static") != "static":
        return None
    keys = config.keys
    # Mirror build_blocks' auto_select reduction to a single key.
    if config.auto_select and keys and len(keys) > 1:
        best_key = select_best_blocking_key(lf, keys, config.max_block_size)
        keys = [best_key]
    if not keys:
        return None

    # PR-5 (autoconfig arrow-port): the per-key block-SIZE reduction now runs on
    # the proven seam ops -- ``derive_block_key`` (the parity-tested twin of
    # ``_build_block_key_expr``), ``filter_valid_key`` (the null/sentinel guard
    # VERBATIM), ``group_len`` (the ``group_by(key).agg(pl.len())`` sizes) --
    # instead of a raw ``pl.Expr`` group_by. Same output on polars AND arrow
    # (pinned by tests/test_blocker_arrow_size_parity.py). Sentinel drop moves
    # AHEAD of the group_by, but that is output-identical here: every row in a
    # block shares the key, so dropping the sentinel/null group == dropping its
    # rows first.
    from goldenmatch.core.frame import is_polars_lazyframe, to_frame

    frame = to_frame(lf.collect()) if is_polars_lazyframe(lf) else to_frame(lf)

    max_block_size = config.max_block_size
    all_sizes: list[int] = []
    # Chao1 inputs for n_blocks richness extrapolation (S1): counted from the
    # raw per-key sizes BEFORE the size<2 drop (singletons feed F1).
    f1 = 0
    f2 = 0
    for key_config in keys:
        keyed = frame.with_column(
            "__block_key__",
            frame.derive_block_key(
                key_config.fields, key_config.transforms or [],
                field_transforms=getattr(key_config, "field_transforms", None),
            ),
        )
        valid = keyed.filter_valid_key("__block_key__")
        # Per-key sizes AFTER the null/sentinel drop, BEFORE the size<2 drop.
        all_key_sizes = [int(v) for v in valid.group_len(["__block_key__"]).column("len").to_list()]
        f1 += sum(1 for s in all_key_sizes if s == 1)
        f2 += sum(1 for s in all_key_sizes if s == 2)
        sizes = [s for s in all_key_sizes if s >= 2]
        # An oversized block is sub-split by _build_static_blocks under BOTH
        # skip_oversized values now (True -> ANN/auto-split/skip; False ->
        # zero-config auto-split when splittable, see #372), so the raw group-by
        # sizes no longer match the built blocks. Bail to the exact path.
        if any(s > max_block_size for s in sizes):
            return None
        all_sizes.extend(sizes)
    return all_sizes, f1, f2


def measure_blocking_profile(
    df: pl.DataFrame | pl.LazyFrame,
    config: Any,
) -> BlockingProfile | None:
    """Measure a ``BlockingProfile`` on the FULL frame (Phase 1: measure, not
    extrapolate). Spec 2026-06-06 §Phase 1.

    Runs the committed config's blocking over all rows and computes the true
    pair count + block-size distribution — the cheap op (blocking was never the
    bottleneck; scoring was, and that is now ~5x faster). Returns ``None`` on
    ANY failure or when the config has no blocking, so the caller can fall back
    to linear extrapolation without risk.

    Fast path (Stage-D, spec 2026-06-22): plain ``static`` blocking computes the
    block-size distribution with a single vectorized ``group_by`` per key
    (``_fast_static_block_sizes``), ~36x faster than building + re-collecting
    every block; non-static configs and oversized-split cases fall back to the
    exact ``build_blocks`` loop. Both paths feed the identical aggregation below.
    """
    try:
        blocking_cfg = getattr(config, "blocking", None)
        if blocking_cfg is None:
            return None
        # PR-5 (autoconfig arrow-port): normalize the input through the seam so
        # a polars frame/lazyframe OR an arrow Table/Frame all measure the same.
        # n_rows comes from ``Frame.height`` (no ``pl.len()`` collect); the seam
        # frame flows into ``_fast_static_block_sizes`` (dual-rep) and, on the
        # exact fallback, into ``build_blocks`` (which ingests a Frame/arrow).
        from goldenmatch.core.frame import is_polars_lazyframe, to_frame

        frame = (
            to_frame(cast("pl.LazyFrame", df).collect())
            if is_polars_lazyframe(df)
            else to_frame(df)
        )
        n_rows: int = frame.height

        if blocking_cfg.passes:
            keys_used = [list(k.fields) for k in blocking_cfg.passes]
        elif blocking_cfg.keys:
            keys_used = [list(k.fields) for k in blocking_cfg.keys]
        else:
            keys_used = []

        fast = _fast_static_block_sizes(frame, blocking_cfg)
        if fast is None:
            # Exact fallback: build every block and collect its length. This path
            # cannot recover the pre-drop singleton/doubleton counts, so the Chao1
            # inputs stay None and extrapolate_to uses the linear n_blocks fallback.
            sizes = []
            for b in build_blocks(frame, blocking_cfg):
                try:
                    sizes.append(b.n_rows())
                except Exception:
                    sizes.append(0)
            chao1_f1: int | None = None
            chao1_f2: int | None = None
        else:
            sizes, chao1_f1, chao1_f2 = fast
        sizes_sorted = sorted(sizes)
        n_blocks = len(sizes_sorted)
        total_comparisons = sum(s * (s - 1) // 2 for s in sizes_sorted)
        max_pairs = n_rows * (n_rows - 1) // 2
        reduction_ratio = 1.0 - total_comparisons / max(1, max_pairs)
        singleton_block_count = sum(1 for s in sizes_sorted if s == 1)
        oversized_block_count = sum(
            1 for s in sizes_sorted if s > blocking_cfg.max_block_size
        )
        return BlockingProfile(
            keys_used=keys_used,
            n_blocks=n_blocks,
            total_comparisons=total_comparisons,
            reduction_ratio=reduction_ratio,
            block_sizes_p50=_percentile(sizes_sorted, 0.50),
            block_sizes_p95=_percentile(sizes_sorted, 0.95),
            block_sizes_p99=_percentile(sizes_sorted, 0.99),
            block_sizes_max=max(sizes_sorted) if sizes_sorted else 0,
            singleton_block_count=singleton_block_count,
            oversized_block_count=oversized_block_count,
            chao1_f1=chao1_f1,
            chao1_f2=chao1_f2,
        )
    except Exception:
        logger.debug(
            "measure_blocking_profile failed; caller should extrapolate.",
            exc_info=True,
        )
        return None


@dataclass
class BlockResult:
    """Result of blocking: a block key and its member rows.

    D5b (arrow descent): ``df`` accepts a legacy ``pl.LazyFrame`` (usually an
    eagerly-collected group re-wrapped lazy), an eager ``pl.DataFrame``, or a
    seam ``Frame``. Consumers use ``materialize()`` / ``n_rows()`` -- the
    representation-agnostic reads -- instead of ``.collect()`` round-trips.
    """

    block_key: str
    df: Any
    strategy: str = "static"
    depth: int = 0
    parent_key: str | None = None
    pre_scored_pairs: list[tuple[int, int, float]] | None = None
    # Fields whose equality created this block. EM uses this provenance to
    # condition only the sampled pairs from this pass, rather than globally
    # neutralizing the union of every multi-pass blocking field.
    blocking_fields: tuple[str, ...] = ()

    def materialize(self):
        """The block's rows as a seam Frame (collects a legacy LazyFrame once)."""
        from goldenmatch.core.frame import Frame, is_polars_lazyframe, to_frame

        d = self.df
        if isinstance(d, Frame):
            return d
        if is_polars_lazyframe(d):  # arrow-port: import-safe LazyFrame guard
            d = d.collect()
        return to_frame(d)

    def n_rows(self) -> int:
        """Row count without a full materialization round-trip at call sites."""
        return self.materialize().height


class RowIdBlock:
    """A blocking result carrying ONLY its member row-ids -- no per-block frame.

    The Fellegi-Sunter EM trainer samples within-block pairs reading nothing but
    ``__row_id__`` and each block's ``blocking_fields`` provenance
    (``probabilistic._sample_blocked_pairs_with_fields``); the sampled pairs'
    field values are looked up on the full score frame, never the blocks. So an
    EM-training block does not need a frame at all -- a compact ``int64`` array
    replaces the ``BlockResult`` group frame, eliminating the FS EM
    ``build_blocks`` memory peak (the per-block-object floor + the per-pass
    full-frame transient). ``materialize()`` builds a 1-column frame ON DEMAND,
    so only the few dozen blocks the sampler actually visits ever pay.

    Interface-compatible with ``BlockResult`` for the EM sampler
    (``materialize().column("__row_id__")``, ``block_key``, ``blocking_fields``,
    ``n_rows()``). NOT for scoring -- these blocks feed EM alone.
    """

    __slots__ = ("block_key", "blocking_fields", "strategy", "_ids")

    def __init__(self, block_key, ids, blocking_fields=(), strategy="static"):
        self.block_key = block_key
        self._ids = ids  # numpy int64 array of __row_id__ values
        self.blocking_fields = blocking_fields
        self.strategy = strategy

    def materialize(self):
        """A 1-column ``__row_id__`` seam Frame, built lazily (sampled blocks only)."""
        from goldenmatch.core.frame import to_frame

        return to_frame(pl.DataFrame({"__row_id__": self._ids}))

    def n_rows(self) -> int:
        return len(self._ids)


def build_em_blocks_agg(frame: Any, config: BlockingConfig) -> list:
    """Field-hash EM-training blocks as compact row-id arrays via ONE
    ``group_by().agg()`` per pass -- NEVER materializing per-block frames.

    Membership matches ``build_blocks``: reuses ``_build_block_key_expr`` + the
    same null/sentinel key filter + the ``multi_pass`` ``(pass_sig, block_key)``
    dedup, so EM's sampled pairs -- hence the ``EMResult`` and the whole run --
    are BYTE-IDENTICAL, absent oversized blocks. (``build_blocks`` auto-splits a
    block over ``max_block_size`` -- a *scoring* optimization; EM's sampler caps
    per-block ids anyway, so keeping oversized blocks whole is a bench-gated
    behavior change on datasets that have them, exact parity where none do.)

    Supports ``static`` / ``multi_pass`` -- what FS auto-config emits (incl. the
    SN bound, which rewrites to static passes). Raises ``NotImplementedError``
    for other strategies so the caller falls back to ``build_blocks``.

    Peak RSS (person 100k, whole pipeline): 2126 -> 549 MB vs ``build_blocks``,
    byte-identical output.
    """
    import numpy as np

    from goldenmatch.core.frame import (
        is_polars_dataframe,
        is_polars_lazyframe,
        to_frame as _tf,
    )

    if config.strategy not in ("static", "multi_pass"):
        raise NotImplementedError(
            f"build_em_blocks_agg supports static/multi_pass, not {config.strategy!r}"
        )

    native = _tf(frame).native
    if is_polars_lazyframe(native):
        lf = native
    elif is_polars_dataframe(native):
        lf = native.lazy()
    else:  # arrow Table
        lf = cast(pl.DataFrame, pl.from_arrow(native)).lazy()

    def _agg_pass(key_config: BlockingKeyConfig, strategy: str) -> list:
        # Same key expr + sentinel filter as _build_static_blocks, but agg the
        # row-ids per key instead of materializing a frame per group. Group and
        # within-group order are irrelevant: the sampler re-sorts by block_key
        # and sorts each block's row_ids before sampling.
        expr = _build_block_key_expr(key_config)
        grouped = (
            lf.with_columns(expr)
            .filter(
                pl.col("__block_key__").is_not_null()
                & ~pl.col("__block_key__")
                    .str.strip_chars()
                    .str.to_lowercase()
                    .is_in(["nan", "null", "none"])
            )
            .group_by("__block_key__")
            .agg(pl.col("__row_id__"))
            .collect()
        )
        fields = tuple(key_config.fields)
        out: list = []
        for key_str, ids in zip(
            grouped["__block_key__"].to_list(),
            grouped["__row_id__"].to_list(),
        ):
            if key_str is None or len(ids) < 2:
                continue
            out.append(
                RowIdBlock(key_str, np.asarray(ids, dtype=np.int64), fields, strategy)
            )
        return out

    if config.strategy == "multi_pass":
        results: list = []
        seen: set = set()
        for pass_config in config.passes or []:
            pass_sig = (
                tuple(pass_config.fields),
                tuple(pass_config.transforms or []),
            )
            for block in _agg_pass(pass_config, "multi_pass"):
                dedup_key = (pass_sig, block.block_key)
                if dedup_key not in seen:
                    results.append(block)
                    seen.add(dedup_key)
        return results

    results = []
    for key_config in config.keys or []:
        results.extend(_agg_pass(key_config, "static"))
    return results


def collect_blocking_fields(config: BlockingConfig) -> list[str]:
    """All column names a blocking config groups on, across keys/passes/sub-blocks.

    Used by the Fellegi-Sunter pipeline to tell EM which fields are blocking
    fields (always-agree within a single-pass block -> no discrimination ->
    excluded from m-training, given fixed neutral weights). For ``multi_pass``
    the keys live in ``passes``, not ``keys`` -- reading only ``keys`` (the old
    behavior) left the exclusion list empty and degraded multi-pass FS
    (Febrl4: 95.7% -> 98.4% F1 once the pass fields are excluded). Order is
    preserved and de-duplicated.
    """
    seen: set[str] = set()
    out: list[str] = []
    groups: list[BlockingKeyConfig] = []
    groups.extend(config.keys or [])
    groups.extend(config.passes or [])
    groups.extend(config.sub_block_keys or [])
    for key in groups:
        for f in key.fields:
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def _build_block_key_expr(key_config: BlockingKeyConfig) -> pl.Expr:
    """Build a block key expression from a BlockingKeyConfig.

    Transforms each field and concatenates with || separator. Uses native
    Polars expressions when every configured transform is vectorizable
    (lowercase, strip, substring, etc. -- see matchkey._try_native_transform).
    Falls back to map_elements only for transforms that need Python
    (soundex, metaphone). The native fast path matters at scale: a 5M-row
    blocking key with map_elements is 10M Python calls and was the root
    cause of the 5M bench hang (RSS climbed 1.5 GB / 30s with no instrumented
    stage closing).
    """
    from goldenmatch.core.matchkey import _try_native_chain

    # Per-field chains (#1826): a field listed in field_transforms uses its
    # OWN chain; others keep the key-level chain. getattr keeps the
    # SimpleNamespace duck-type callers (frame.derive_block_key) working.
    per_field = getattr(key_config, "field_transforms", None) or {}
    field_exprs: list[pl.Expr] = []
    for field_name in key_config.fields:
        transforms = per_field.get(field_name, key_config.transforms or [])
        native = _try_native_chain(field_name, transforms) if transforms else None
        if native is not None:
            field_exprs.append(native)
        elif transforms:
            field_exprs.append(
                pl.col(field_name).map_elements(
                    lambda val, transforms=transforms: apply_transforms(val, transforms),
                    return_dtype=pl.Utf8,
                )
            )
        else:
            field_exprs.append(pl.col(field_name).cast(pl.Utf8))

    if len(field_exprs) == 1:
        return field_exprs[0].alias("__block_key__")
    else:
        return pl.concat_str(field_exprs, separator="||").alias("__block_key__")


def _build_static_blocks(lf: Any, config: BlockingConfig) -> list[BlockResult]:
    """Build static blocks — original blocking logic.

    Groups records by each blocking key, skipping blocks with < 2 records
    and handling oversized blocks per config.skip_oversized.

    Hot-block auto-split (2026-05-15): when a block exceeds
    ``max_block_size`` and no ANN column is configured, attempt
    ``_auto_split_block`` before silently skipping. The static-mode
    path used to drop these blocks on the floor — measured at 94% of
    wall on a 100K zero-config run because a single 1158-record
    last-name block dominated the per-block ``cdist`` quadratic. Hot
    splitting trades one quadratic block for multiple smaller blocks
    whose summed quadratic work is dramatically lower.
    """
    from goldenmatch.core.bench import record_metric

    results: list[BlockResult] = []
    hot_blocks_split = 0
    hot_blocks_skipped = 0

    for key_config in config.keys:
        # Add block key column AND filter null/sentinel keys in a single
        # lazy pipeline so Polars' optimizer fuses the filter with the
        # projection. The eager `df.filter(...)` after `.collect()` form
        # used to peak at ~2x the frame size (one for the materialized
        # collect, one for the filtered result) -- enough to push a
        # 1.13M-row × 58-col frame past an 8 GB sandbox cap (#375).
        #
        # NULL filter rationale (originally #372): records with NULL in
        # the blocking column shouldn't cluster together. The cast(Utf8)
        # in _build_block_key_expr turns Polars NaN into the literal
        # string "NaN"; a downstream lowercase transform collapses that
        # to "nan", and ~12K NULL records would otherwise share a single
        # block and OOM during scoring. The group-by-level
        # `if key_str is None: continue` only catches Polars None, not
        # the stringified forms.
        # Filter ``nan``/``null``/``none`` sentinel keys -- those come
        # from Polars' cast(Utf8) on NULL or NaN values, NOT from
        # legitimate user-typed strings. Empty strings ("") are kept
        # because they're real values (an explicit empty-cell in the
        # source); dropping them aggressively lost 3 records on the
        # cross-file dedupe regression suite (PR #390 fix).
        from goldenmatch.core.frame import is_polars_lazyframe

        if is_polars_lazyframe(lf):
            # Polars-only: the native expr chain (pl.col) is built lazily here so
            # the arrow branch below (derive_block_key) never imports polars.
            block_key_expr = _build_block_key_expr(key_config)
            df_with_key = (
                lf
                .with_columns(block_key_expr)
                .filter(
                    pl.col("__block_key__").is_not_null()
                    & ~pl.col("__block_key__")
                        .str.strip_chars()
                        .str.to_lowercase()
                        .is_in(["nan", "null", "none"])
                )
                .collect()
            )
        else:
            # D2s-a: seam Frame (or eager native) entry -- derive_block_key is
            # the twin of _build_block_key_expr; filter_valid_key is the
            # sentinel guard verbatim. The lazy branch above stays raw Polars:
            # its fused with_columns+filter+collect is RSS-load-bearing (#375)
            # and legacy callers hand genuinely-lazy frames.
            from goldenmatch.core.frame import to_frame as _tf

            _f = _tf(lf)
            _f = _f.with_column(
                "__block_key__",
                _f.derive_block_key(
                    key_config.fields, key_config.transforms or [],
                    field_transforms=getattr(key_config, "field_transforms", None)
                ),
            )
            df_with_key = _f.filter_valid_key("__block_key__").native

        # Group by block key (W2c/W2d seam: group_partitions is the
        # hash-grouped twin of group_by iteration -- deterministic
        # first-appearance order, blocks are an unordered set downstream).
        # The lazy with_columns+filter+collect ABOVE stays raw Polars: the
        # fused pipeline is RSS-load-bearing (#375) and this entry can
        # receive genuinely-lazy frames.
        from goldenmatch.core.frame import to_frame

        for key_str, group_frame in to_frame(df_with_key).group_partitions("__block_key__"):
            group_df = group_frame.native
            if key_str is None:
                continue

            size = len(group_df)

            if size < 2:
                continue

            if size > config.max_block_size:
                if config.skip_oversized and config.ann_column:
                    # ANN fallback: embed oversized block's records and sub-block
                    try:
                        ann_sub = _ann_sub_block(
                            group_df, config.ann_column, config.ann_top_k,
                            config.ann_model, config.max_block_size, key_str,
                        )
                        if ann_sub:
                            for sub_block in ann_sub:
                                sub_block.blocking_fields = tuple(key_config.fields)
                            results.extend(ann_sub)
                    except Exception:
                        logger.error(
                            "ANN sub-blocking failed for block %r (%d records). Skipping block.",
                            key_str, size, exc_info=True,
                        )
                    continue
                elif config.skip_oversized:
                    # Hot-block auto-split: try recovering via
                    # highest-cardinality column before giving up.
                    # `_auto_split_block` returns at least one
                    # BlockResult per useful sub-group; if it can't
                    # split meaningfully it returns the parent block,
                    # which we then skip (preserves prior behavior).
                    try:
                        sub_blocks = _auto_split_block(
                            group_df,
                            config.max_block_size,
                            key_str,
                            tuple(key_config.fields),
                        )
                    except Exception:
                        logger.error(
                            "Hot-block auto-split failed for %r (%d records). Skipping.",
                            key_str, size, exc_info=True,
                        )
                        hot_blocks_skipped += 1
                        continue
                    # "Useful" sub-blocks are those genuinely smaller than
                    # the parent. _auto_split_block can return a single
                    # sub-block that's still the full parent size when no
                    # column has cardinality > 1 within the block — that's
                    # the "couldn't split" sentinel.
                    useful_subs: list[BlockResult] = []
                    for b in sub_blocks:
                        try:
                            sub_size = b.n_rows()
                        except Exception:
                            sub_size = size + 1  # treat unknown as not-useful
                        if sub_size < size and sub_size >= 2:
                            useful_subs.append(b)
                    if useful_subs:
                        results.extend(useful_subs)
                        hot_blocks_split += 1
                        logger.info(
                            "Hot-block split %r (%d records) → %d sub-blocks",
                            key_str, size, len(useful_subs),
                        )
                        continue
                    # Fall through to the original skip behavior when
                    # no useful sub-blocks could be produced.
                    logger.warning(
                        f"Block {key_str!r} has {size} records "
                        f"(exceeds max_block_size={config.max_block_size}) "
                        "and auto-split produced no useful sub-blocks. Skipping."
                    )
                    hot_blocks_skipped += 1
                    continue
                elif config.sub_block_keys:
                    # The adaptive strategy splits this oversized block by its
                    # configured ``sub_block_keys`` downstream (build_blocks'
                    # adaptive path calls _sub_block). Leave it intact here so we
                    # don't pre-empt that with the zero-config auto-split; the
                    # block is NOT scored whole because the caller sub-blocks it.
                    logger.debug(
                        "Oversized block %r (%d records) left intact for "
                        "configured sub_block_keys splitting.",
                        key_str, size,
                    )
                else:
                    # skip_oversized=False. Attempt the SAME zero-config
                    # hot-block auto-split first (sub-partition by the
                    # highest-cardinality column) -- splitting preserves recall
                    # AND avoids the O(n^2) scoring OOM, so it is strictly better
                    # than scoring the whole mega-block. This is the default path
                    # (auto-config's FS year-diversify pass and the #1784-kept
                    # common-surname block produce 10k+ record blocks whose
                    # ~100M+ pairs OOM'd the runner when scored whole). Only when
                    # auto-split can't help do we honor the opt-in "process
                    # anyway".
                    try:
                        sub_blocks = _auto_split_block(
                            group_df,
                            config.max_block_size,
                            key_str,
                            tuple(key_config.fields),
                        )
                    except Exception:
                        logger.error(
                            "Hot-block auto-split failed for %r (%d records).",
                            key_str, size, exc_info=True,
                        )
                        sub_blocks = []
                    useful_subs = []
                    for b in sub_blocks:
                        try:
                            sub_size = b.n_rows()
                        except Exception:
                            sub_size = size + 1  # treat unknown as not-useful
                        if sub_size < size and sub_size >= 2:
                            useful_subs.append(b)
                    if useful_subs:
                        results.extend(useful_subs)
                        hot_blocks_split += 1
                        logger.info(
                            "Hot-block split %r (%d records) → %d sub-blocks",
                            key_str, size, len(useful_subs),
                        )
                        continue
                    # Auto-split couldn't help -- honor the opt-in and process
                    # the whole block (log at ERROR so the OOM-vs-correctness
                    # tradeoff is obvious in the run log).
                    logger.error(
                        f"Block {key_str!r} has {size} records "
                        f"(exceeds max_block_size={config.max_block_size}, "
                        f"~{size * (size - 1) // 2:,} pairs to score) and "
                        f"auto-split produced no useful sub-blocks. Processing "
                        f"anyway because skip_oversized=False; set "
                        f"blocking.skip_oversized=True to skip oversized blocks "
                        f"instead of risking OOM. See #372."
                    )

            results.append(BlockResult(
                block_key=key_str,
                df=group_df,  # D5b: eager (was group_df.lazy() -- a re-wrap)
                blocking_fields=tuple(key_config.fields),
            ))

    if hot_blocks_split or hot_blocks_skipped:
        record_metric("hot_blocks_split_count", hot_blocks_split)
        record_metric("hot_blocks_skipped_count", hot_blocks_skipped)

    return results


def _ann_sub_block(
    block_df: pl.DataFrame,
    ann_column: str,
    ann_top_k: int,
    ann_model: str,
    max_block_size: int,
    parent_key: str,
) -> list[BlockResult]:
    """ANN fallback for oversized blocks.

    Embeds only the unique text values in the block, maps embeddings back
    to all records, then uses FAISS to find neighbors and create sub-blocks.
    """
    from goldenmatch.core.ann_blocker import ANNBlocker
    from goldenmatch.core.cluster import UnionFind
    from goldenmatch.core.embedder import get_embedder

    size = len(block_df)

    # Cap: only ANN sub-block moderately oversized blocks (up to 10x max_block_size)
    # Truly massive blocks (60K+) would still be too expensive to embed
    if size > max_block_size * 10:
        logger.info(
            "ANN fallback: block %r has %d records (>%dx max). Too large, skipping.",
            parent_key, size, 10,
        )
        return []

    if ann_column not in block_df.columns:
        logger.warning(
            "ANN fallback: column %r not in block %r. Skipping %d records.",
            ann_column, parent_key, size,
        )
        return []

    # Deduplicate texts — embed only unique values
    all_texts = block_df[ann_column].to_list()
    unique_texts = list(set(t for t in all_texts if t is not None and str(t).strip()))

    if len(unique_texts) < 2:
        logger.info("ANN fallback: block %r has <2 unique texts. Skipping.", parent_key)
        return []

    logger.info(
        "ANN fallback: block %r has %d records, %d unique texts. Embedding...",
        parent_key, size, len(unique_texts),
    )

    embedder = get_embedder(ann_model)
    unique_embeddings = embedder.embed_column(
        unique_texts, cache_key=f"ann_sub_{parent_key}",
    )

    # Map unique embeddings back to all records
    text_to_idx = {t: i for i, t in enumerate(unique_texts)}
    record_indices = []  # index into unique_embeddings for each record
    valid_records = []   # indices into block_df that have valid text
    for i, t in enumerate(all_texts):
        if t is not None and str(t).strip() and t in text_to_idx:
            record_indices.append(text_to_idx[t])
            valid_records.append(i)

    if len(valid_records) < 2:
        return []

    import numpy as np
    record_embeddings = unique_embeddings[np.array(record_indices)]

    # Build FAISS index and query
    blocker = ANNBlocker(top_k=min(ann_top_k, len(valid_records) - 1))
    blocker.build_index(record_embeddings)
    pairs = blocker.query(record_embeddings)

    # Group into sub-blocks via Union-Find
    _row_ids = block_df["__row_id__"].to_list()
    uf = UnionFind()
    for a, b in pairs:
        real_a = valid_records[a]
        real_b = valid_records[b]
        uf.add(real_a)
        uf.add(real_b)
        uf.union(real_a, real_b)

    clusters = uf.get_clusters()
    results: list[BlockResult] = []
    n_oversized = 0
    for members in clusters:
        if len(members) < 2:
            continue
        member_list = sorted(members)
        if len(member_list) > max_block_size:
            n_oversized += 1
            logger.warning(
                "ANN sub-block from %r still has %d records (> max %d). Skipping.",
                parent_key, len(member_list), max_block_size,
            )
            continue
        sub_df = block_df[member_list]
        results.append(BlockResult(
            block_key=f"{parent_key}_ann_{min(member_list)}",
            df=sub_df.lazy(),
            strategy="ann",
        ))

    logger.info(
        "ANN fallback: block %r -> %d sub-blocks (%d still oversized)",
        parent_key, len(results), n_oversized,
    )
    return results


def _sub_block(
    block_df: pl.DataFrame,
    sub_block_keys: list[BlockingKeyConfig],
    max_block_size: int,
    depth: int,
    parent_key: str,
) -> list[BlockResult]:
    """Recursively sub-block an oversized block using sub_block_keys.

    Args:
        block_df: The oversized block DataFrame.
        sub_block_keys: Remaining sub-block keys to try.
        max_block_size: Maximum block size threshold.
        depth: Current recursion depth (1-indexed).
        parent_key: The parent block key value.

    Returns:
        List of BlockResult with adaptive metadata.
    """
    if depth > 3 or not sub_block_keys:
        # Max depth reached or no more keys — return as-is with warning
        logger.warning(
            f"Sub-block of {parent_key!r} has {len(block_df)} records at depth {depth}. "
            f"No further sub-blocking possible. Processing anyway."
        )
        return [BlockResult(
            block_key=parent_key,
            df=block_df.lazy(),
            strategy="adaptive",
            depth=depth,
            parent_key=parent_key,
        )]

    current_key_config = sub_block_keys[0]
    remaining_keys = sub_block_keys[1:]

    block_key_expr = _build_block_key_expr(current_key_config)
    df_with_key = block_df.with_columns(block_key_expr)

    groups = df_with_key.group_by("__block_key__")
    results: list[BlockResult] = []

    for key, group_df in groups:
        key_str = key[0]
        if key_str is None:
            continue

        size = len(group_df)

        if size < 2:
            continue

        if size > max_block_size and remaining_keys and depth < 3:
            # Recurse with next sub_block_key
            sub_results = _sub_block(
                group_df,
                remaining_keys,
                max_block_size,
                depth + 1,
                parent_key,
            )
            results.extend(sub_results)
        else:
            if size > max_block_size:
                logger.warning(
                    f"Sub-block {key_str!r} of {parent_key!r} has {size} records at depth {depth}. "
                    f"Processing anyway."
                )
            results.append(BlockResult(
                block_key=key_str,
                df=group_df.lazy(),
                strategy="adaptive",
                depth=depth,
                parent_key=parent_key,
            ))

    return results


def _auto_split_block(
    block_df: Any,  # pl.DataFrame | pa.Table | seam Frame -- normalized via to_frame
    max_block_size: int,
    parent_key: str,
    blocking_fields: tuple[str, ...] = (),
) -> list[BlockResult]:
    """Auto-split an oversized block using the highest-cardinality column.

    When no sub_block_keys are configured, this provides a zero-config fallback
    that splits by the column with the most unique values.
    """
    from goldenmatch.core.frame import to_frame

    # Seam-normalize FIRST: block_df may be a pa.Table (arrow-native path) whose
    # ``.columns`` are ChunkedArrays, not names -- ``bframe.columns`` returns
    # NAMES on both backends. (The raw ``block_df.columns`` here was the
    # arrow-path AttributeError that made auto-split silently no-op and let the
    # mega-block fall through to be scored whole -> OOM. See #372/#1790.)
    bframe = to_frame(block_df)
    candidates = [c for c in bframe.columns if not c.startswith("__")]
    if not candidates:
        logger.warning(
            "Auto-split of %r: no non-internal columns available. Processing as-is.",
            parent_key,
        )
        return [BlockResult(
            block_key=parent_key,
            df=bframe.native,
            strategy="adaptive",
            depth=1,
            parent_key=parent_key,
            blocking_fields=blocking_fields,
        )]

    # Pick column whose cardinality best splits blocks near max_block_size.
    # Ideal: each group has ~max_block_size records.
    # Score = number of groups with >= 2 records (useful groups).
    n = bframe.height
    best_col = candidates[0]
    best_useful_groups = 0
    best_nunique = 0

    for col in candidates:
        nunique = bframe.column(col).n_unique()
        # Estimate: if we split by this column, avg group size = n / nunique
        avg_group = n / nunique if nunique > 0 else n
        # Count groups that will have >= 2 records (useful for matching):
        # derive the Utf8-cast key via the seam, group, count sizes >= 2.
        cast_key = bframe.derive_transformed_column(col, [])
        sized = bframe.with_column("__auto_probe__", cast_key).group_len(["__auto_probe__"])
        useful_groups = sum(1 for c in sized.column("len").to_list() if c >= 2)

        if useful_groups > best_useful_groups or (
            useful_groups == best_useful_groups and avg_group <= max_block_size and nunique > best_nunique
        ):
            best_useful_groups = useful_groups
            best_nunique = nunique
            best_col = col

    # W2d seam: cast-key attach (derive_transformed_column with an empty
    # chain IS the Utf8 cast) + hash-grouped iteration; null keys skipped
    # explicitly, exactly as the raw loop did.
    keyed = bframe.with_column("__auto_split__", bframe.derive_transformed_column(best_col, []))

    results: list[BlockResult] = []
    for key_str, group_frame in keyed.group_partitions("__auto_split__"):
        if key_str is None:
            continue
        if group_frame.height < 2:
            continue
        if group_frame.height > max_block_size:
            logger.warning(
                "Auto-split sub-block %r of %r has %d records (still oversized). Processing anyway.",
                key_str, parent_key, group_frame.height,
            )
        results.append(BlockResult(
            block_key=f"{parent_key}||{key_str}",
            df=group_frame.drop(["__auto_split__"]).native,
            strategy="adaptive",
            depth=1,
            parent_key=parent_key,
            blocking_fields=tuple(dict.fromkeys((*blocking_fields, best_col))),
        ))

    logger.info(
        "Auto-split %r (%d records) into %d sub-blocks using column %r (cardinality=%d)",
        parent_key, bframe.height, len(results), best_col, best_nunique,
    )
    return results if results else [BlockResult(
        block_key=parent_key,
        df=bframe.native,
        strategy="adaptive",
        depth=1,
        parent_key=parent_key,
        blocking_fields=blocking_fields,
    )]


def _build_sorted_neighborhood_blocks(
    lf: pl.LazyFrame, config: BlockingConfig,
) -> list[BlockResult]:
    """Build sorted neighborhood blocks with a sliding window.

    For each SortKeyField in config.sort_key, transform the column and
    concatenate into a sort key. Collect, sort, then slide a window through.
    """
    if not config.sort_key:
        raise ValueError("sorted_neighborhood strategy requires sort_key configuration.")

    # Build sort key expression
    sort_field_exprs = []
    for skf in config.sort_key:
        if skf.transforms:
            expr = pl.col(skf.column).map_elements(
                lambda val, transforms=skf.transforms: apply_transforms(val, transforms),
                return_dtype=pl.Utf8,
            )
        else:
            expr = pl.col(skf.column).cast(pl.Utf8)
        sort_field_exprs.append(expr)

    if len(sort_field_exprs) == 1:
        sort_key_expr = sort_field_exprs[0].alias("__sort_key__")
    else:
        sort_key_expr = pl.concat_str(sort_field_exprs, separator="||").alias("__sort_key__")

    # Collect and sort. Drop rows whose sort key is missing FIRST: a null (or
    # nan/null/none sentinel) sort key means "this row cannot be ordered", not
    # "it neighbors every other unorderable row". Without this, null-key rows
    # sort adjacent and window together into spurious candidate blocks (#1859).
    # filter_valid_key is the blocker's guard verbatim -- it keeps "" (#390).
    from goldenmatch.core.frame import to_frame as _tf

    df = (
        _tf(lf.with_columns(sort_key_expr).collect())
        .filter_valid_key("__sort_key__")
        .native.sort("__sort_key__")
    )
    n = len(df)
    window_size = config.window_size

    results: list[BlockResult] = []

    if n <= window_size:
        # Dataset smaller than window — single block
        if n >= 2:
            results.append(BlockResult(
                block_key="sorted_window_0",
                df=df.lazy(),
                strategy="sorted_neighborhood",
            ))
        return results

    # Slide window through sorted data
    for i in range(n - window_size + 1):
        window_df = df.slice(i, window_size)
        results.append(BlockResult(
            block_key=f"sorted_window_{i}",
            df=window_df.lazy(),
            strategy="sorted_neighborhood",
        ))

    return results


def _build_multi_pass_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list[BlockResult]:
    """Run multiple blocking passes and union candidate blocks.

    Each pass uses a different BlockingKeyConfig. Blocks with duplicate keys
    across passes are deduplicated so each unique block key appears once.
    """
    all_blocks: list[BlockResult] = []
    # Dedup by (pass field+transform signature, block_key value) — NOT the
    # value alone. block_key is the concatenated field *values* with no field
    # identity, so a value-only dedup collides ACROSS passes: a
    # given_name/soundex block "s530" and a surname/soundex block "s530" share
    # the string, and the second was silently dropped — losing every candidate
    # pair in it (measured: 309/7310 blocks, 4.2%, dropped on Febrl4's
    # auto-config scheme; soundex/substring/numeric keys share a namespace).
    # Keying on the pass signature dedups only *truly identical* blocks (same
    # fields+transforms+value, e.g. two passes that happen to share a key) while
    # keeping distinct-field blocks that merely share a value string.
    seen_keys: set[tuple] = set()

    for pass_config in config.passes or []:
        temp_config = BlockingConfig(
            keys=[pass_config],
            max_block_size=config.max_block_size,
            skip_oversized=config.skip_oversized,
            ann_column=config.ann_column,
            ann_top_k=config.ann_top_k,
            ann_model=config.ann_model,
        )
        pass_sig = (tuple(pass_config.fields), tuple(pass_config.transforms or []))
        blocks = _build_static_blocks(lf, temp_config)
        for block in blocks:
            dedup_key = (pass_sig, block.block_key)
            if dedup_key not in seen_keys:
                block.strategy = "multi_pass"
                all_blocks.append(block)
                seen_keys.add(dedup_key)

    return all_blocks


def _build_ann_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list[BlockResult]:
    """Build blocks using ANN (approximate nearest neighbor) on embeddings.

    Embeds the configured column, queries top-K neighbors with FAISS,
    then groups connected pairs into micro-blocks via Union-Find.
    """
    from goldenmatch.core.ann_blocker import ANNBlocker
    from goldenmatch.core.cluster import UnionFind
    from goldenmatch.core.embedder import get_embedder

    if not config.ann_column:
        raise ValueError("ANN blocking requires 'ann_column' to be set.")

    df = lf.collect()
    values = df[config.ann_column].to_list()

    embedder = get_embedder(config.ann_model)
    embeddings = embedder.embed_column(values, cache_key=f"ann_{config.ann_column}")

    blocker = ANNBlocker(top_k=config.ann_top_k)
    blocker.build_index(embeddings)
    pairs = blocker.query(embeddings)

    # Group nearby records into micro-blocks using Union-Find
    uf = UnionFind()
    for a, b in pairs:
        uf.add(a)
        uf.add(b)
        uf.union(a, b)

    clusters = uf.get_clusters()
    results: list[BlockResult] = []
    for members in clusters:
        if len(members) < 2:
            continue
        member_list = sorted(members)
        # `members` are positions in `df` (UnionFind operates on the same
        # indices that drive the embeddings array, which is row-aligned with
        # df). Direct positional indexing is O(K) vs filter(is_in(...))'s
        # O(N) per block — at 1M rows with ~50K blocks this was the dominant
        # wall cost (50% of total via PyLazyFrame.collect; cProfile Round 5).
        # `row_ids` lookup was redundant indirection.
        block_df = df[member_list]
        results.append(BlockResult(
            block_key=f"ann_{min(member_list)}",
            df=block_df.lazy(),
            strategy="ann",
        ))

    return results


def _build_ann_pair_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list[BlockResult]:
    """Build direct-pair ANN blocks without Union-Find.

    Returns a single BlockResult with pre_scored_pairs set.
    FAISS similarity scores are propagated directly.
    """
    from goldenmatch.core.ann_blocker import ANNBlocker
    from goldenmatch.core.embedder import get_embedder

    if not config.ann_column:
        raise ValueError("ann_pairs blocking requires 'ann_column' to be set.")

    df = lf.collect()
    values = df[config.ann_column].to_list()

    embedder = get_embedder(config.ann_model)
    embeddings = embedder.embed_column(values, cache_key=f"ann_{config.ann_column}")

    blocker = ANNBlocker(top_k=config.ann_top_k)
    blocker.build_index(embeddings)
    scored_pairs = blocker.query_with_scores(embeddings)

    # Map positional indices to __row_id__ values
    row_ids = df["__row_id__"].to_list()
    mapped_pairs = [
        (int(row_ids[a]), int(row_ids[b]), score)
        for a, b, score in scored_pairs
    ]

    return [BlockResult(
        block_key="ann_pairs",
        df=df.lazy(),
        strategy="ann_pairs",
        pre_scored_pairs=mapped_pairs,
    )]


def _build_learned_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list[BlockResult]:
    """Build blocks using learned predicates.

    Two-pass approach:
    1. If cached rules exist, load and apply them
    2. Otherwise, run a fast sample with static blocking to generate training pairs,
       then learn predicates from those pairs
    """
    from goldenmatch.core.learned_blocking import (
        apply_learned_blocks,
        learn_blocking_rules,
        load_learned_rules,
        save_learned_rules,
    )

    # Try loading cached rules
    if config.learned_cache_path:
        cached = load_learned_rules(config.learned_cache_path)
        if cached:
            logger.info("Using cached learned blocking rules from %s", config.learned_cache_path)
            return apply_learned_blocks(lf, cached, config.max_block_size)

    # Pass 1: fast static blocking on first key to generate training pairs
    df = lf.collect()
    sample_size = min(config.learned_sample_size, df.height)
    if sample_size < df.height:
        sample_df = df.sample(sample_size, seed=42)
    else:
        sample_df = df

    # Use static blocking with the configured keys for the sample run
    sample_config = config.model_copy(update={"strategy": "static"})
    sample_blocks = _build_static_blocks(sample_df.lazy(), sample_config)

    # Score sample blocks to get training pairs
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.scorer import find_fuzzy_matches

    # Build a simple weighted matchkey for scoring
    cols = [c for c in df.columns if not c.startswith("__")]
    if not cols:
        return _build_static_blocks(lf, sample_config)

    # Use first few columns for a quick score
    score_fields = [
        MatchkeyField(field=c, scorer="token_sort", weight=1.0, transforms=["lowercase"])
        for c in cols[:3]
    ]
    score_mk = MatchkeyConfig(name="_learned_score", type="weighted", threshold=0.5, fields=score_fields)

    scored_pairs = []
    for block in sample_blocks:
        block_df = block.materialize().native
        pairs = find_fuzzy_matches(block_df, score_mk)
        scored_pairs.extend(pairs)

    if not scored_pairs:
        logger.warning("No scored pairs from sample run. Falling back to static blocking.")
        return _build_static_blocks(lf, sample_config)

    # Pass 2: learn rules from scored pairs
    rules = learn_blocking_rules(
        sample_df,
        scored_pairs,
        columns=cols,
        min_recall=config.learned_min_recall,
        min_reduction=config.learned_min_reduction,
        predicate_depth=config.learned_predicate_depth,
    )

    # Cache rules
    if config.learned_cache_path and rules:
        save_learned_rules(rules, config.learned_cache_path)
        logger.info("Saved learned blocking rules to %s", config.learned_cache_path)

    # Apply to full dataset
    return apply_learned_blocks(lf, rules, config.max_block_size)


def _build_canopy_blocks(lf: pl.LazyFrame, config: BlockingConfig) -> list[BlockResult]:
    """Build blocks using TF-IDF canopy clustering.

    Forms overlapping canopies based on cosine similarity of TF-IDF vectors.
    Records can appear in multiple canopies.
    """
    from goldenmatch.core.canopy import build_canopies

    if not config.canopy:
        raise ValueError("Canopy blocking requires 'canopy' config to be set.")

    df = lf.collect()
    canopy_cfg = config.canopy

    # Concatenate canopy fields into a single text value per record
    text_values = []
    for row in df.iter_rows(named=True):
        parts = [str(row.get(f, "") or "") for f in canopy_cfg.fields]
        text_values.append(" ".join(parts))

    canopies = build_canopies(
        text_values,
        loose_threshold=canopy_cfg.loose_threshold,
        tight_threshold=canopy_cfg.tight_threshold,
        max_canopy_size=canopy_cfg.max_canopy_size,
    )

    results: list[BlockResult] = []
    for i, members in enumerate(canopies):
        if len(members) < 2:
            continue
        # `members` are positions in `df` — `build_canopies` returns
        # indices into the text_values list which was built by enumerating
        # df.iter_rows in order. Direct positional indexing is O(K) vs
        # filter(is_in(...))'s O(N) per canopy — see ann_blocking_strategy
        # for the cProfile attribution that drove this change.
        block_df = df[sorted(list(members))]
        results.append(BlockResult(
            block_key=f"canopy_{i}",
            df=block_df.lazy(),
            strategy="canopy",
        ))

    return results


def select_best_blocking_key(
    lf: pl.LazyFrame,
    keys: list[BlockingKeyConfig],
    max_block_size: int = 5000,
) -> BlockingKeyConfig:
    """Evaluate blocking keys and select the one with smallest max block size.

    Computes group-size histogram for each candidate key, then picks the key
    that minimizes max_group_size while maintaining >= 50% coverage.
    """
    if len(keys) <= 1:
        return keys[0]

    df = lf.collect()
    total = len(df)

    best_key = keys[0]
    best_max_size = float("inf")

    for key_config in keys:
        block_key_expr = _build_block_key_expr(key_config)
        df_with_key = df.with_columns(block_key_expr)

        # Count non-null block keys (coverage)
        non_null = df_with_key.filter(pl.col("__block_key__").is_not_null()).height
        coverage = non_null / total if total > 0 else 0.0

        if coverage < 0.5:
            logger.debug(
                "Auto-select: skipping key %s (coverage %.1f%% < 50%%)",
                key_config.fields, coverage * 100,
            )
            continue

        # Compute group sizes
        groups = df_with_key.filter(pl.col("__block_key__").is_not_null()).group_by("__block_key__").agg(
            pl.len().alias("size")
        )
        # polars Series.max() returns PythonLiteral; "size" is i64 at runtime.
        max_size = int(groups["size"].max() or 0)  # pyright: ignore[reportArgumentType]  # polars max() typed as PythonLiteral; "size" is int64 at runtime
        group_count = groups.height

        logger.debug(
            "Auto-select: key %s -> groups=%d, max_size=%d, coverage=%.1f%%",
            key_config.fields, group_count, max_size, coverage * 100,
        )

        if max_size < best_max_size or (max_size == best_max_size and group_count > 0):
            best_max_size = max_size
            best_key = key_config

    logger.info(
        "Auto-select: chose key %s (max_block_size=%d)",
        best_key.fields, best_max_size,
    )
    return best_key


def build_blocks(lf: Any, config: BlockingConfig) -> list[BlockResult]:
    """Build blocks from a LazyFrame based on blocking configuration.

    Routes by config.strategy:
    - "static": original blocking behavior
    - "adaptive": primary blocks + recursive sub-blocking for oversized blocks
    - "sorted_neighborhood": sliding window over sorted data
    - "ann": ANN blocking with FAISS on embeddings
    - "canopy": TF-IDF canopy clustering

    Args:
        lf: Input LazyFrame.
        config: Blocking configuration with keys, max_block_size, skip_oversized.

    Returns:
        List of BlockResult, one per valid block.
    """
    # D2s-a: every non-static strategy + key auto-select + profile emission
    # still takes a polars LazyFrame, so a seam Frame (or eager native)
    # normalizes ONCE here (lossless -- polars stays a dependency until D6).
    # Only the static/adaptive primary builder is dual-rep; it receives the
    # ORIGINAL entry object so the arrow lane skips the polars round-trip.
    from goldenmatch.core.frame import is_polars_dataframe, is_polars_lazyframe

    _lf_entry = lf
    if not is_polars_lazyframe(lf):
        from goldenmatch.core.frame import Frame, to_frame

        native = lf.native if isinstance(lf, Frame) else lf
        if is_polars_dataframe(native):
            lf = native.lazy()
        else:
            # PR-5 (autoconfig arrow-port): arrow ingest routes through the seam
            # (no manual ``pl.from_arrow`` unwrap). The static/adaptive primary
            # builder consumes ``_lf_entry`` directly via ``derive_block_key``
            # (~line 406), so the polars round-trip is only needed by the
            # non-static strategies, ``select_best_blocking_key`` auto-select,
            # and an active profile emitter. When none of those apply, keep a
            # seam Frame and skip the materialization entirely; otherwise fall
            # back to the arrow->polars conversion (the seam has no arrow->polars
            # op, so that conversion still uses pyarrow under the hood).
            # multi_pass is dual-rep too: it delegates each pass to
            # _build_static_blocks (seam-native via derive_block_key), so it stays
            # arrow when no polars-only feature (multi-key auto-select) is in
            # play. This is the common zero-config shape (the #1207 per-identifier
            # union), so keeping it off the polars round trip is the load-bearing
            # blocking-spine eviction for zero-config.
            # NOTE: an active profile emitter (the auto-config controller) no
            # longer forces polars here -- ``_emit_blocking_profile`` reads the
            # row count off the arrow seam's ``height()`` (polars-free), so the
            # controller's sample iterations run arrow-native on a base install
            # without polars (previously they errored -> degraded config). The
            # emitted BlockingProfile is byte-identical (row count is row count).
            _needs_polars = (
                config.strategy not in ("static", "adaptive", "multi_pass")
                or (config.auto_select and config.keys and len(config.keys) > 1)
            )
            lf = (
                cast(pl.DataFrame, pl.from_arrow(native)).lazy()
                if _needs_polars
                else to_frame(native)
            )

    # Auto-select: pick best key based on histogram analysis
    if config.auto_select and config.keys and len(config.keys) > 1:
        best_key = select_best_blocking_key(lf, config.keys, config.max_block_size)
        config = config.model_copy(update={"keys": [best_key], "auto_select": False})

    if config.strategy == "learned":
        blocks = _build_learned_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "canopy":
        blocks = _build_canopy_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "ann_pairs":
        blocks = _build_ann_pair_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "ann":
        blocks = _build_ann_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "sorted_neighborhood":
        blocks = _build_sorted_neighborhood_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "multi_pass":
        # Mirror static/adaptive: consume the ORIGINAL seam entry so the arrow
        # lane skips the polars round-trip (each pass runs _build_static_blocks,
        # which is dual-rep). `lf` (polars, when an emitter forced the round-trip)
        # is still what _emit_blocking_profile consumes.
        blocks = _build_multi_pass_blocks(_lf_entry, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "lsh":
        from goldenmatch.core.lsh_blocker import build_lsh_blocks

        blocks = build_lsh_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "simhash":
        from goldenmatch.core.simhash_blocker import build_simhash_blocks

        blocks = build_simhash_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "perceptual":
        from goldenmatch.core.perceptual_blocker import build_perceptual_blocks

        blocks = build_perceptual_blocks(lf, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    if config.strategy == "static":
        blocks = _build_static_blocks(_lf_entry, config)
        _emit_blocking_profile(blocks, config, lf)
        return blocks

    # strategy == "adaptive"
    primary_blocks = _build_static_blocks(_lf_entry, config)
    sub_block_keys = config.sub_block_keys or []

    results: list[BlockResult] = []
    for block in primary_blocks:
        block_df = block.materialize().native
        size = len(block_df)

        if size > config.max_block_size and sub_block_keys:
            sub_results = _sub_block(
                block_df,
                sub_block_keys,
                config.max_block_size,
                depth=1,
                parent_key=block.block_key,
            )
            results.extend(sub_results)
        elif size > config.max_block_size and not config.skip_oversized:
            # Auto-split: no sub_block_keys configured, split by highest-cardinality column
            auto_results = _auto_split_block(block_df, config.max_block_size, block.block_key)
            results.extend(auto_results)
        else:
            results.append(block)

    _emit_blocking_profile(results, config, lf)
    return results
