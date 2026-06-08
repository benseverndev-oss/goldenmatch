"""Fast per-pair Fellegi-Sunter scoring for the bucket scorer's fast path.

Sister module to `probabilistic.py::score_probabilistic` -- same scoring
semantics (m/u-trained match weights, normalized to [0,1], thresholded
by `mk.link_threshold` or the computed default), but pre-resolves all
per-pair work at gate time so the scoring loop never:

- Calls `score_field` (PluginRegistry dispatch per pair per field)
- Calls `apply_transforms` per pair (the precomputed `__xform_<sig>__`
  columns already encode the transformed values; this module reads them
  directly)
- Builds a per-row dict via `to_dicts()` (row_lookup in the slow path)
- Looks up field values by name in the per-pair inner loop

Gate eligibility (`_resolve_probabilistic_fast_path`):
  - mk.type == "probabilistic"
  - For every mk.field:
      - scorer resolves via `_resolve_score_pair_callable`, OR is `ensemble`
        (special-cased to `_ensemble_score_single` -- the bucket/weighted fast
        path declines ensemble, but the probabilistic path uses the same scalar
        ensemble as its own slow path, so prob-fast == prob-slow holds)
      - `__xform_<sig>__` column exists in `prepared_df`
      - field.levels in {2, 3} (N>3 unsupported in fast path v1; falls back
        to the slow `score_probabilistic` path)
  - em_result has match_weights for every field

When the gate fails, callers fall back to `score_probabilistic`. The fast
path produces bit-equivalent output within rapidfuzz tolerance (parity
asserted in `tests/test_fast_path_probabilistic.py`).

Design parallels `_score_one_bucket_fast` in `backends/score_buckets.py`:
pre-resolved spec at gate time + indexed Python loop over pre-materialized
arrays at run time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from goldenmatch.backends.score_buckets import _resolve_score_pair_callable

if TYPE_CHECKING:
    from goldenmatch.config.schemas import MatchkeyConfig
    from goldenmatch.core.probabilistic import EMResult


# Per-field spec for the probabilistic fast path:
#   (xform_col, score_pair_fn, levels, partial_threshold, weights_per_level)
# weights_per_level is em_result.match_weights[field], a list whose length
# equals levels (so we can index by level without dict lookup).
ProbFieldSpec = tuple[str, Any, int, float, list[float]]


def _resolve_probabilistic_fast_path(
    mk: MatchkeyConfig,
    prepared_df: pl.DataFrame,
    em_result: EMResult,
) -> tuple[list[ProbFieldSpec], float, float, float, float] | None:
    """Decide whether mk + em_result is eligible for the fast path and
    pre-resolve every per-field plan needed by the inner loop.

    Returns (field_specs, link_threshold, max_weight, min_weight, weight_range)
    when eligible; None when any gate fails (caller falls back to slow path).

    Eligibility gates (conservative -- the slow path remains correct for
    everything not handled here):
      - mk.type == "probabilistic"
      - For every mk.field: scorer resolves via _resolve_score_pair_callable
        (rules out embedding/record_embedding/unknown plugins) OR is `ensemble`
        (special-cased to _ensemble_score_single; see module docstring) AND its
        precomputed xform column is in prepared_df AND levels in {2, 3}.
      - em_result has match_weights for every field.
    """
    from goldenmatch.core.matchkey import _xform_sig
    from goldenmatch.core.probabilistic import compute_thresholds

    if mk.type != "probabilistic":
        return None
    if not mk.fields:
        return None

    field_specs: list[ProbFieldSpec] = []
    max_weight = 0.0
    min_weight = 0.0
    for f in mk.fields:
        # Per-pair callable. None when scorer is model-backed or unknown.
        # `ensemble` is special-cased: the bucket/weighted fast path
        # deliberately DECLINES it (its per-pair ensemble diverged from the
        # MATRIX ensemble used by find_fuzzy_matches), but the probabilistic
        # path never touches the matrix ensemble -- its scalar source of truth
        # is score_field(a, b, "ensemble") == _ensemble_score_single, which the
        # slow path (comparison_vector) also uses. So matching prob-fast to
        # prob-slow here is safe and keeps the bucket/weighted decline intact.
        if f.scorer == "ensemble":
            from goldenmatch.core.scorer import _ensemble_score_single
            fn = _ensemble_score_single
        else:
            fn = _resolve_score_pair_callable(f.scorer)
            if fn is None:
                return None
        # Precomputed xform column must exist (precompute_matchkey_transforms
        # iterates all matchkey fields, including probabilistic ones).
        xform_col = _xform_sig(f)
        if xform_col not in prepared_df.columns:
            return None
        # Fast path v1 supports 2 + 3 level fields. N > 3 falls back so the
        # slow path's even-spaced threshold logic stays the source of truth.
        if f.levels not in (2, 3):
            return None
        # em_result must have weights for this field.
        weights = em_result.match_weights.get(f.field)
        if not weights or len(weights) != f.levels:
            return None
        field_specs.append((
            xform_col,
            fn,
            int(f.levels),
            float(f.partial_threshold),
            [float(w) for w in weights],
        ))
        max_weight += max(weights)
        min_weight += min(weights)

    # Resolve threshold the same way the slow path does.
    if mk.link_threshold is not None:
        link_threshold = float(mk.link_threshold)
    else:
        link_threshold, _ = compute_thresholds(em_result)
        link_threshold = float(link_threshold)

    weight_range = max_weight - min_weight
    return field_specs, link_threshold, max_weight, min_weight, weight_range


def score_probabilistic_fast(
    block_df: pl.DataFrame,
    spec: tuple[list[ProbFieldSpec], float, float, float, float],
    exclude_pairs: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int, float]]:
    """Score pairs in a block using a pre-resolved probabilistic spec.

    Bit-equivalent (within rapidfuzz tolerance) to:

        score_probabilistic(block_df, mk, em_result, exclude_pairs)

    when the gate accepted the (mk, em_result, block_df) triple. The
    per-pair work is:

      for each (i, j):
        for each field k:
          sim_k = score_fn_k(xform_arr_k[i], xform_arr_k[j])
          level_k = map_to_level(sim_k, levels_k, partial_threshold_k)
          weight_sum += weights_k[level_k]
        normalized = (weight_sum - min_weight) / weight_range
        if normalized >= link_threshold: emit

    No PluginRegistry dispatch, no per-pair dict construction, no
    apply_transforms (precomputed xform columns already encode the
    transformed values).
    """
    if exclude_pairs is None:
        exclude_pairs = set()

    field_specs, link_threshold, _max_weight, min_weight, weight_range = spec
    n_fields = len(field_specs)
    row_ids = block_df["__row_id__"].to_list()
    n_rows = len(row_ids)
    if n_rows < 2:
        return []

    # Pre-materialize all field columns + their plans as parallel arrays so
    # the inner loop is pure index work.
    xform_arrays: list[list[Any]] = []
    score_fns: list[Any] = []
    levels_list: list[int] = []
    partial_thresholds: list[float] = []
    weights_list: list[list[float]] = []
    for xform_col, fn, levels, partial_threshold, weights in field_specs:
        xform_arrays.append(block_df[xform_col].to_list())
        score_fns.append(fn)
        levels_list.append(levels)
        partial_thresholds.append(partial_threshold)
        weights_list.append(weights)

    results: list[tuple[int, int, float]] = []
    for i in range(n_rows):
        ri = row_ids[i]
        for j in range(i + 1, n_rows):
            rj = row_ids[j]
            if ri < rj:
                pair_key = (ri, rj)
            else:
                pair_key = (rj, ri)
            if pair_key in exclude_pairs:
                continue

            weight_sum = 0.0
            for k in range(n_fields):
                va = xform_arrays[k][i]
                vb = xform_arrays[k][j]
                if va is None or vb is None:
                    # Slow path treats nulls as disagree (level 0).
                    weight_sum += weights_list[k][0]
                    continue
                sim = score_fns[k](va, vb)
                if sim is None:
                    weight_sum += weights_list[k][0]
                    continue
                lvls = levels_list[k]
                pt = partial_thresholds[k]
                # Map similarity to level. Same logic as comparison_vector
                # in core/probabilistic.py:67-77 -- 2-level binary threshold,
                # 3-level uses 0.95 as the high cutoff. N > 3 was filtered
                # out at gate time.
                if lvls == 2:
                    level = 1 if sim >= pt else 0
                else:  # lvls == 3
                    if sim >= 0.95:
                        level = 2
                    elif sim >= pt:
                        level = 1
                    else:
                        level = 0
                weight_sum += weights_list[k][level]

            # Normalize and threshold. Identical to score_probabilistic's
            # final block.
            if weight_range > 0:
                normalized = (weight_sum - min_weight) / weight_range
            else:
                normalized = 0.5
            if normalized >= link_threshold:
                results.append((pair_key[0], pair_key[1], round(normalized, 4)))

    return results
