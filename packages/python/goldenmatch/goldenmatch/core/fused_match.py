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

    get_mks = getattr(config, "get_matchkeys", None)
    mks = get_mks() if callable(get_mks) else []
    if len(mks) != 1:
        return False
    mk = mks[0]
    if getattr(mk, "type", None) != "weighted":
        return False
    if getattr(mk, "negative_evidence", None):
        return False
    if mk.threshold is None or not mk.fields:
        return False
    for f in mk.fields:
        if not f.field:
            return False
        if f.scorer not in _FUSED_SCORER_IDS:
            return False
    return True


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

    # clusters: list[list[int]] connected components (incl singletons).
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
