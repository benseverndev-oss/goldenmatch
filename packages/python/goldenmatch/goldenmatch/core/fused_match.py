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


def _prep_frame(columns: Mapping[str, Any], src_cols: list[str]) -> Any:
    """The fused prep's Frame, selected EXPLICITLY by `resolve_frame_backend()`.

    Default (`polars`): today's exact round-trip -- `pl.DataFrame` from the
    Arrow columns, frame-wide Utf8 cast -- zero behavior change. `arrow`: an
    ArrowFrame over `pa.table` (the seam ops cast per-column). `to_frame()` is
    deliberately NOT the selector here: its dict coercion is unconditionally
    Arrow, which would silently flip the default fused path.
    """
    from goldenmatch.core.frame import ArrowFrame, PolarsFrame, resolve_frame_backend

    if resolve_frame_backend() == "arrow":
        return ArrowFrame(pa.table({c: columns[c] for c in src_cols}))

    import polars as pl

    return PolarsFrame(pl.DataFrame({c: columns[c] for c in src_cols}).cast(pl.Utf8))


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
    # W2a: derivation runs through the Frame seam -- polars-free end to end
    # under GOLDENMATCH_FRAME=arrow, byte-identical Polars delegation otherwise.
    frame = _prep_frame(columns, src_cols)

    key_arrs = [
        frame.derive_block_key(
            key_cfg.fields, key_cfg.transforms or [],
            field_transforms=getattr(key_cfg, "field_transforms", None),
        ).to_arrow()
    ]  # already transformed + "||"-concatenated
    score_arrs = [
        frame.derive_transformed_column(f.field, f.transforms or []).to_arrow()
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
    whose fields all use an FS-native scorer (`_NATIVE_FS_SCORER_IDS`).
    Transforms covered (derived host-side). `run_match_fused_fs_arrow` takes a
    PRE-TRAINED `EMResult` — training is the caller's O(n) model fit, unchanged.

    Two features are additionally gated on the LOADED kernel's capability
    consts (goldenmatch-native >= 0.1.15); when the config uses NEITHER, the
    gate stays pure-config and never probes the native module:

    - Custom `level_thresholds` banding: ready only when the kernel exposes
      `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS`. Older wheels band with the
      hard-coded default banding only, so the lists must never cross their
      FFI — those environments decline to the Python paths.
    - Negative evidence (`mk.negative_evidence`): ready only when the kernel
      exposes `FS_SUPPORTS_NE` AND every NE scorer is FS-native (an
      `ensemble`-scorer NE field declines) AND no NE field uses `derive_from`.
      The derive_from decline exists because `run_match_fused_fs_arrow` takes
      a raw `columns` mapping and never runs `precompute_matchkey_transforms`
      (it builds `src_cols` from blocking keys + matchkey/NE fields only), so
      a derive_from-SYNTHESIZED `ne.field` would not exist in the frame and
      NE would silently never fire; declining keeps parity with the classic
      path, which synthesizes derive_from columns upstream — that is why
      `_fs_native_eligible` does NOT decline the same matchkey. (The
      Arrow-lane `derive_ne_joined` seam is the future synthesize option if
      fused derive_from coverage is ever wanted.)
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

    mk = mks[0]
    ne_fields = getattr(mk, "negative_evidence", None) or []
    uses_level_thresholds = any(
        getattr(f, "level_thresholds", None) is not None for f in mk.fields
    )
    if not all(f.field and f.scorer in _NATIVE_FS_SCORER_IDS for f in mk.fields):
        return False
    for ne in ne_fields:
        if ne.scorer not in _NATIVE_FS_SCORER_IDS or ne.derive_from:
            return False
    if ne_fields or uses_level_thresholds:
        # Capability probe ONLY when a gated feature is in play — the gate is
        # pure-config otherwise. Local import (not the module-level binding)
        # so the loaded module is resolved at call time.
        try:
            from goldenmatch.core._native_loader import native_module as _nm

            mod = _nm()
        except Exception:
            return False
        if mod is None:
            return False
        if ne_fields and not getattr(mod, "FS_SUPPORTS_NE", False):
            return False
        if uses_level_thresholds and not getattr(
            mod, "FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", False
        ):
            return False
    return True


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
    thresholds, plus the capability-gated `level_thresholds` + NE kwarg groups)
    are assembled EXACTLY as `score_probabilistic_native` does — so the fused
    kernel scores identically to the pipeline's native FS block scorer.
    Returns a `(__row_id__, __cluster_id__)` Table, or None if uncovered / native
    absent.
    """
    from goldenmatch.core.probabilistic import (
        _NATIVE_FS_SCORER_IDS,
        _field_values_from_list,
        _fs_calibration_mode,
        compute_thresholds,
        fs_weight_range,
        prior_weight,
    )

    fn = _match_fused_fs_symbol()
    if fn is None or not match_fused_fs_ready(config):
        return None

    key_cfg = config.blocking.keys[0]
    mk = config.get_matchkeys()[0]
    ne_fields = mk.negative_evidence or []
    src_cols = list(
        dict.fromkeys(
            [
                *key_cfg.fields,
                *[f.field for f in mk.fields],
                # NE columns ride along; an NE field absent from `columns` is
                # skipped here and degrades to all-null below (never fires).
                *[ne.field for ne in ne_fields if ne.field in columns],
            ]
        )
    )
    n = n_rows if n_rows is not None else len(columns[src_cols[0]])
    frame = _prep_frame(columns, src_cols)

    key_arrs = [
        frame.derive_block_key(
            key_cfg.fields, key_cfg.transforms or [],
            field_transforms=getattr(key_cfg, "field_transforms", None),
        ).to_arrow()
    ]

    # FS kernel args — mirrors score_probabilistic_native exactly.
    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0
    # NE-aware weight envelope: the centralized fs_weight_range covers the
    # `__ne__` entries / penalty_bits contributions a hand-rolled per-field
    # min/max sum would miss (which would mis-normalize every fused NE score).
    min_w, max_w = fs_weight_range(em_result, mk)
    weight_range = max_w - min_w
    if mk.link_threshold is not None:
        link_threshold = float(mk.link_threshold)
    else:
        link_threshold, _ = compute_thresholds(em_result, calibrated=calibrated)
    scorer_ids = [_NATIVE_FS_SCORER_IDS[f.scorer] for f in mk.fields]
    levels = [int(f.levels) for f in mk.fields]
    partials = [float(f.partial_threshold) for f in mk.fields]
    weights = [[float(w) for w in em_result.match_weights[f.field]] for f in mk.fields]
    # FS prep: extraction goes through the seam (`utf8_values` = Utf8 cast +
    # to_list on either backend); the transform loop is the same pure-Python
    # `_field_values_from_list` the classic FS block scorer uses.
    score_arrs = []
    for f in mk.fields:
        raw = frame.utf8_values(f.field) if f.field in frame.columns else None
        vals = _field_values_from_list(raw, f, n)
        score_arrs.append(pa.array(vals, type=pa.large_string()))

    # Optional capability kwargs — each group is sent ONLY when the matchkey
    # actually uses the feature, so an old wheel never sees an unknown kwarg
    # even if the readiness gate ever drifted (the #1752 discipline; same
    # opt_kwargs shape as score_probabilistic_native).
    opt_kwargs: dict = {}

    # Custom banding (FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS).
    level_thresholds = [
        list(f.level_thresholds) if f.level_thresholds is not None else None
        for f in mk.fields
    ]
    if any(t is not None for t in level_thresholds):
        opt_kwargs["level_thresholds"] = level_thresholds

    # Negative evidence (FS_SUPPORTS_NE). Prep mirrors the score_arrs loop:
    # seam extraction + the same pure-Python transform pass. An NE field
    # absent from `columns` IS reachable here — the readiness gate is
    # pure-config and never validates the caller-supplied columns mapping —
    # so the all-null fallback below is deliberate: NE never fires rather
    # than raising, matching the classic path's missing-column behavior.
    # w_fired mirrors _ne_scalar_contribution: -abs(penalty_bits)
    # when set, else the EM-learned __ne__<field> fired weight (a missing
    # entry raising KeyError matches the scalar path's contract; validate_for
    # guarantees it exists).
    if ne_fields:
        ne_arrs = []
        for ne in ne_fields:
            raw = frame.utf8_values(ne.field) if ne.field in frame.columns else None
            ne_arrs.append(
                pa.array(_field_values_from_list(raw, ne, n), type=pa.large_string())
            )
        opt_kwargs["ne_fields"] = ne_arrs
        opt_kwargs["ne_scorer_ids"] = [
            _NATIVE_FS_SCORER_IDS[ne.scorer] for ne in ne_fields
        ]
        opt_kwargs["ne_thresholds"] = [float(ne.threshold) for ne in ne_fields]
        opt_kwargs["ne_weights"] = [
            -abs(float(ne.penalty_bits)) if ne.penalty_bits is not None
            else float(em_result.match_weights[f"__ne__{ne.field}"][0])
            for ne in ne_fields
        ]

    row_ids = pa.array(range(n), type=pa.int64())
    clusters = fn(
        row_ids, key_arrs, score_arrs, scorer_ids, levels, partials, weights,
        calibrated, prior_w, min_w, weight_range, link_threshold,
        **opt_kwargs,
    )
    return _clusters_to_table(clusters)
