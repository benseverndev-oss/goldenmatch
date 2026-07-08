"""Fused Arrow-native match stage -- opt-in scale/composability entry.

`match_fused` (goldenmatch-native) runs block + score + dedup + cluster in ONE
FFI call, holding every intermediate as a Rust `Vec` instead of a Polars frame
or a Python pairs-list. MEASURED (`bench-match-fused`, 1M-10M on a 64GB box,
scoring parallel on both paths): **wall-neutral** vs the per-stage pipeline
(1.00-1.10x) but **~2x lower peak RSS** (2.73 GB vs 5.19 GB at 10M), clusters
byte-identical. So this is a MEMORY/scale + composability entry -- a single
Arrow-in -> Arrow-out match stage GoldenPipe can thread with no `pl.DataFrame` at
the boundary, which raises the row-count ceiling on a given box ~2x. It is NOT a
speed win and must not be sold as one.

Covered configs run native; everything else returns None so the caller keeps the
existing pipeline (the columnar-decline pattern). Transforms ARE covered: the
block-key + score columns are derived host-side via the pipeline's own transform
reference (`_build_block_key_expr` / `_get_transformed_values`) before the kernel
groups/scores, so any configured transform (lowercase/strip/substring/soundex/…)
is applied byte-identically. The derivation is an O(n) column pass — it does not
reintroduce the O(k^2) block/pairs/dedup materialization the fusion removes, so
the ~2x peak-RSS win (2x single-box capacity) holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from goldenmatch.core._native_loader import native_module

# score_one ids -- mirror backends.score_buckets._NATIVE_SCORER_IDS exactly so
# the fused entry scores identically to the per-stage arrow scorer.
_FUSED_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0,
    "levenshtein": 1,
    "token_sort": 2,
    "exact": 3,
}


def match_fused_ready(config: Any) -> bool:
    """Covered boundary for the fused kernel.

    Covered: `static` single-key blocking + exactly one `weighted` matchkey whose
    fields all use a covered scorer + a threshold. **Transforms are covered** —
    `run_match_fused_arrow` derives the block-key column via the same
    `_build_block_key_expr` the pipeline's blocking uses (transform chain + "||"
    concat) and the score columns via the scorer's own `_get_transformed_values`,
    so any transform the pipeline applies is applied identically here (byte-parity
    by construction; the kernel then groups/scores those derived values). Declines
    probabilistic / ANN / negative-evidence / multi-pass / domain / LLM / PPRL to
    the existing pipeline.
    """
    b = getattr(config, "blocking", None)
    if b is None or getattr(b, "strategy", "static") != "static":
        return False
    keys = getattr(b, "keys", None) or []
    if len(keys) != 1:
        return False
    if getattr(b, "ann_column", None):
        return False
    return _covered_weighted_matchkey(config) is not None


def _covered_weighted_matchkey(config: Any) -> Any | None:
    """The single covered `weighted` matchkey, or None. Covered = one weighted
    matchkey, a threshold, every field named + on a covered scorer, no NE."""
    get_mks = getattr(config, "get_matchkeys", None)
    mks = get_mks() if callable(get_mks) else []
    if len(mks) != 1:
        return None
    mk = mks[0]
    if getattr(mk, "type", None) != "weighted":
        return None
    if getattr(mk, "negative_evidence", None):
        return None
    if mk.threshold is None or not mk.fields:
        return None
    for f in mk.fields:
        if not f.field or f.scorer not in _FUSED_SCORER_IDS:
            return None
    return mk


def match_fused_multipass_ready(config: Any) -> bool:
    """Covered boundary for the fused MULTI-PASS blocking path: `multi_pass`
    blocking with >=1 pass (each a real blocking key) + one covered weighted
    matchkey. Each pass is run as a single-key fused match; their per-pass
    clusters are union-find-merged (CC of the pass-pair union)."""
    b = getattr(config, "blocking", None)
    if b is None or getattr(b, "strategy", None) != "multi_pass":
        return False
    if getattr(b, "ann_column", None):
        return False
    passes = getattr(b, "passes", None) or []
    if not passes or any(not getattr(p, "fields", None) for p in passes):
        return False
    return _covered_weighted_matchkey(config) is not None


def _match_fused_symbol() -> Any | None:
    try:
        mod = native_module()
    except Exception:
        return None
    return getattr(mod, "match_fused", None)


def run_match_fused_arrow(
    columns: Mapping[str, Any],
    config: Any,
    n_rows: int | None = None,
) -> Any | None:
    """Run the fused match stage over Arrow columns.

    Returns a pyarrow Table ``(__row_id__ int64, __cluster_id__ int64)`` -- one
    row per input record, a stable cluster id per connected component -- or
    ``None`` when the config is not covered OR the native kernel is absent, so
    the caller falls back to the existing pipeline.

    ``columns`` maps SOURCE field name -> pyarrow Array (the untransformed input
    columns for every blocking + matchkey field). The block-key and score columns
    are DERIVED here via the pipeline's own transform reference
    (`_build_block_key_expr` for the key, `_get_transformed_values` for each score
    field), so any configured transform is applied byte-identically before the
    kernel groups/scores. Row ids are 0..n in input order.
    """
    import polars as pl

    from goldenmatch.core.blocker import _build_block_key_expr
    from goldenmatch.core.scorer import _get_transformed_values

    fn = _match_fused_symbol()
    if fn is None or not match_fused_ready(config):
        return None

    key_cfg = config.blocking.keys[0]
    mk = config.get_matchkeys()[0]
    src_cols = list(dict.fromkeys([*key_cfg.fields, *[f.field for f in mk.fields]]))

    n = n_rows if n_rows is not None else len(columns[src_cols[0]])
    # One O(n) pass to derive the transformed key + score columns, exactly as the
    # pipeline would (blocking pre-casts every column to Utf8; mirror that). This
    # does NOT reintroduce the block/pairs/dedup materialization the fusion
    # eliminates -- it's a few string columns, not the O(k^2) match state.
    frame = pl.DataFrame({c: columns[c] for c in src_cols}).cast(pl.Utf8)

    block_key = (
        frame.lazy().select(_build_block_key_expr(key_cfg)).collect().get_column("__block_key__")
    )
    key_arrs = [block_key.to_arrow()]  # already transformed + "||"-concatenated
    score_arrs = [
        pl.Series(f.field, _get_transformed_values(frame, f), dtype=pl.Utf8).to_arrow()
        for f in mk.fields
    ]

    row_ids = pa.array(range(n), type=pa.int64())
    scorer_ids = [_FUSED_SCORER_IDS[f.scorer] for f in mk.fields]
    weights = [float(f.weight if f.weight is not None else 1.0) for f in mk.fields]
    total_weight = sum(weights)
    threshold = float(mk.threshold)

    clusters = fn(row_ids, key_arrs, score_arrs, scorer_ids, weights, total_weight, threshold)
    return _clusters_to_table(clusters)


def _clusters_to_table(clusters: Any) -> Any:
    """list[list[int]] connected components (incl singletons) -> pyarrow Table
    (__row_id__, __cluster_id__), one row per record, stable id per component."""
    rid: list[int] = []
    cid: list[int] = []
    for c_id, comp in enumerate(clusters):
        for r in comp:
            rid.append(r)
            cid.append(c_id)
    return pa.table(
        {
            "__row_id__": pa.array(rid, type=pa.int64()),
            "__cluster_id__": pa.array(cid, type=pa.int64()),
        }
    )


def run_match_fused_multipass_arrow(
    columns: Mapping[str, Any],
    config: Any,
    n_rows: int | None = None,
) -> Any | None:
    """Fused multi-pass blocking: run each pass as a single-key fused match, then
    union-find-merge the per-pass clusters. Byte-parity with the pipeline's
    multi-pass path: the final partition is the connected components of the union
    of all passes' candidate pairs, which equals merging each pass's per-pass
    clusters (every member of a pass-cluster is transitively connected). Returns a
    `(__row_id__, __cluster_id__)` Table, or None if uncovered / native absent.
    """
    from goldenmatch.config.schemas import BlockingConfig, GoldenMatchConfig

    if _match_fused_symbol() is None or not match_fused_multipass_ready(config):
        return None

    n = n_rows if n_rows is not None else len(next(iter(columns.values())))
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    mks = config.get_matchkeys()
    for pass_cfg in config.blocking.passes:
        single = GoldenMatchConfig(
            blocking=BlockingConfig(strategy="static", keys=[pass_cfg]),
            matchkeys=mks,
        )
        tbl = run_match_fused_arrow(columns, single, n_rows=n)
        if tbl is None:
            continue
        groups: dict[int, list[int]] = {}
        for r, c in zip(
            tbl.column("__row_id__").to_pylist(), tbl.column("__cluster_id__").to_pylist()
        ):
            groups.setdefault(c, []).append(r)
        for members in groups.values():
            for m in members[1:]:
                union(members[0], m)

    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return _clusters_to_table(list(comps.values()))


def match_fused_fs_ready(config: Any) -> bool:
    """Covered boundary for the fused Fellegi-Sunter (probabilistic) path.

    Covered: `static` single-key blocking + exactly one `probabilistic` matchkey
    whose fields all use an FS-native scorer (`_NATIVE_FS_SCORER_IDS`). Transforms
    covered (derived host-side). `run_match_fused_fs_arrow` takes a PRE-TRAINED
    `EMResult` — training is the caller's O(n) model fit, unchanged.
    """
    b = getattr(config, "blocking", None)
    if b is None or getattr(b, "strategy", "static") != "static":
        return False
    keys = getattr(b, "keys", None) or []
    if len(keys) != 1 or getattr(b, "ann_column", None):
        return False
    get_mks = getattr(config, "get_matchkeys", None)
    mks = get_mks() if callable(get_mks) else []
    if len(mks) != 1 or getattr(mks[0], "type", None) != "probabilistic" or not mks[0].fields:
        return False
    from goldenmatch.core.probabilistic import _NATIVE_FS_SCORER_IDS

    return all(f.field and f.scorer in _NATIVE_FS_SCORER_IDS for f in mks[0].fields)


def _match_fused_fs_symbol() -> Any | None:
    try:
        return getattr(native_module(), "match_fused_fs", None)
    except Exception:
        return None


def run_match_fused_fs_arrow(
    columns: Mapping[str, Any],
    config: Any,
    em_result: Any,
    n_rows: int | None = None,
) -> Any | None:
    """Fused Fellegi-Sunter match over Arrow columns, given a trained `EMResult`.

    Byte-parity by construction: the block key is derived via
    `_build_block_key_expr`, the score columns via `_field_values_for_block`, and
    the FS kernel args (levels/partial_thresholds/match_weights/calibration/
    thresholds) are assembled EXACTLY as `score_probabilistic_native` does — so
    the fused kernel scores identically to the pipeline's native FS block scorer.
    Returns a `(__row_id__, __cluster_id__)` Table, or None if uncovered / native
    absent.
    """
    import polars as pl

    from goldenmatch.core.blocker import _build_block_key_expr
    from goldenmatch.core.probabilistic import (
        _NATIVE_FS_SCORER_IDS,
        _field_values_for_block,
        _fs_calibration_mode,
        compute_thresholds,
        prior_weight,
    )

    fn = _match_fused_fs_symbol()
    if fn is None or not match_fused_fs_ready(config):
        return None

    key_cfg = config.blocking.keys[0]
    mk = config.get_matchkeys()[0]
    src_cols = list(dict.fromkeys([*key_cfg.fields, *[f.field for f in mk.fields]]))
    n = n_rows if n_rows is not None else len(columns[src_cols[0]])
    frame = pl.DataFrame({c: columns[c] for c in src_cols}).cast(pl.Utf8)

    block_key = (
        frame.lazy().select(_build_block_key_expr(key_cfg)).collect().get_column("__block_key__")
    )
    key_arrs = [block_key.to_arrow()]

    # FS kernel args — mirrors score_probabilistic_native exactly.
    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0
    max_w = sum(max(em_result.match_weights[f.field]) for f in mk.fields)
    min_w = sum(min(em_result.match_weights[f.field]) for f in mk.fields)
    weight_range = max_w - min_w
    if mk.link_threshold is not None:
        link_threshold = float(mk.link_threshold)
    else:
        link_threshold, _ = compute_thresholds(em_result, calibrated=calibrated)
    scorer_ids = [_NATIVE_FS_SCORER_IDS[f.scorer] for f in mk.fields]
    levels = [int(f.levels) for f in mk.fields]
    partials = [float(f.partial_threshold) for f in mk.fields]
    weights = [[float(w) for w in em_result.match_weights[f.field]] for f in mk.fields]
    score_arrs = [
        pl.Series(f.field, _field_values_for_block(frame, f, n), dtype=pl.Utf8).to_arrow()
        for f in mk.fields
    ]

    row_ids = pa.array(range(n), type=pa.int64())
    clusters = fn(
        row_ids, key_arrs, score_arrs, scorer_ids, levels, partials, weights,
        calibrated, prior_w, min_w, weight_range, link_threshold,
    )
    return _clusters_to_table(clusters)
