"""Cross-pass candidate-pair dedup for Fellegi-Sunter scoring.

Gated behind ``GOLDENMATCH_FS_PAIR_DEDUP=1`` (default OFF). A multi_pass FS
config emits the SAME within-block pair from every pass it co-blocks in, and the
bucket scorer re-scores each copy (the duplicate ``(a, b, score)`` tuples
collapse downstream in ``build_clusters``' ``pair_scores`` dict). Measured on
``historical_50k``: 8 passes emit ~2.96M candidate pairs but only ~1.60M are
distinct — 1.86x redundancy, ~46% of FS scoring re-scores a pair another pass
already produced.

An FS pair's score is a function of the two rows' comparison vector over the
matchkey fields ONLY — it does not depend on which blocking pass co-blocked the
pair — so cross-pass duplicates are IDENTICAL tuples and deduping the candidate
set + scoring once is exactly equivalent to the downstream max-collapse
(parity-preserving by construction; verified byte-identical, see
``tests/test_fs_pair_dedup.py``).

Design (must stay VECTORISED to beat the bucket route's per-block NxN matrices):

  1. Enumerate DISTINCT candidate pairs across every pass (per-pass block keys
     via ``_build_block_key_expr``; oversized blocks skipped exactly like
     ``score_buckets`` / ``build_blocks``), canonicalised + deduped as int64
     row-id arrays.
  2. Per matchkey field, gather each row's value as a small integer CODE
     (factorised distinct transformed values). Pair similarity is then a
     value-pair dedup over CODE pairs — ``np.unique`` over the distinct
     ``(code_a, code_b)`` combinations, score each once, gather back — so no
     P-length object array is ever built (the memory that would otherwise fight
     the bucket route's bounded design).
  3. Levels / weights / normalise / threshold as 1-D numpy over the pair arrays,
     reusing the exact ``score_probabilistic_vectorized`` machinery, then emit.

Measured (native, 1 host): 50k historical −21% wall / RSS flat; 1M realistic
−11% wall / −7% RSS; byte-identical clusters. The lever also removes the bucket
partition overhead, so on the tested shapes peak RSS does not regress.

Safety: the distinct-pair set IS materialised (the bucket route never holds it),
so a pathologically dense block set could blow memory. ``_pair_count_cap`` bounds
it — above the cap the caller falls back to the bucket route (``eligible`` still
True, but the scorer signals ``None`` so the dispatch keeps its default path).

SCOPE: the core FS path — plain fields, null handling, full-range normalisation,
require-positive-evidence, posterior calibration. TF-adjustment / negative-
evidence / (record_)embedding configs DECLINE (``fs_pair_dedup_eligible`` →
False), so the flag is a safe no-op there and the bucket route runs unchanged.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np

from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig


def fs_pair_dedup_enabled() -> bool:
    return os.environ.get("GOLDENMATCH_FS_PAIR_DEDUP", "0").strip().lower() in (
        "1", "true", "on", "yes", "enabled",
    )


def _pair_count_cap() -> int:
    """Max distinct candidate pairs to materialise before declining to the bucket
    route (memory guard). ``GOLDENMATCH_FS_PAIR_DEDUP_MAX_PAIRS``, default 200M
    (~3.2 GB of int64 pair arrays + per-field code/sim arrays)."""
    try:
        return max(1, int(os.environ.get("GOLDENMATCH_FS_PAIR_DEDUP_MAX_PAIRS", "200000000")))
    except ValueError:
        return 200_000_000


def fs_pair_dedup_eligible(mk: MatchkeyConfig, blocking: BlockingConfig | None) -> bool:
    """Core FS path only. Decline (→ bucket-route fallback) for TF / negative-
    evidence / embedding configs and non field-hash blocking strategies (the same
    surface the bucket scorer re-derives from field hashes)."""
    if mk.type != "probabilistic":
        return False
    if getattr(mk, "negative_evidence", None):
        return False
    if blocking is None or getattr(blocking, "strategy", None) not in ("static", "multi_pass"):
        return False
    for f in mk.fields:
        if f.scorer in ("record_embedding", "embedding"):
            return False
        if getattr(f, "tf_adjustment", False):
            return False
    return True


def _distinct_candidate_pairs(
    frame, blocking: BlockingConfig, *, skip_oversized: bool, max_block_size: int, cap: int
) -> tuple[np.ndarray, np.ndarray] | None:
    """All DISTINCT within-block ``(lo, hi)`` row-id pairs across every pass,
    canonicalised + deduplicated. Returns ``None`` if the pre-dedup pair count
    exceeds ``cap`` (memory guard → caller falls back to the bucket route)."""
    import polars as pl

    from goldenmatch.core.blocker import _build_block_key_expr

    passes = blocking.passes or blocking.keys or []
    a_parts: list[np.ndarray] = []
    b_parts: list[np.ndarray] = []
    running = 0
    for key in passes:
        kf = (
            frame.select(
                pl.col("__row_id__").cast(pl.Int64),
                _build_block_key_expr(key).alias("__bk__"),
            )
            .filter(pl.col("__bk__").is_not_null() & (pl.col("__bk__") != ""))
            .group_by("__bk__")
            .agg(pl.col("__row_id__"))
        )
        for ids in kf["__row_id__"].to_list():
            n = len(ids)
            if n < 2 or (skip_oversized and n > max_block_size):
                continue
            running += n * (n - 1) // 2
            if running > cap:
                return None
            arr = np.asarray(ids, dtype=np.int64)
            ia, ib = np.triu_indices(n, 1)
            a_parts.append(arr[ia])
            b_parts.append(arr[ib])
    if not a_parts:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    a = np.concatenate(a_parts)
    b = np.concatenate(b_parts)
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    stride = np.int64(int(hi.max()) + 1)
    uniq = np.unique(lo * stride + hi)
    return (uniq // stride).astype(np.int64), (uniq % stride).astype(np.int64)


def _field_codes(frame, f, n_rows: int) -> tuple[np.ndarray, list, int]:
    """``(codes_by_rowid, value_table, null_code)`` — each row's transformed field
    value as a small integer code (distinct transformed values factorised).
    ``__row_id__`` is the pipeline's dense 0-based index, so ``codes_by_rowid`` is
    indexed directly by row id. Building CODES (not P-length value arrays) is the
    memory win over a naive gather."""
    import polars as pl

    from goldenmatch.core.probabilistic import _transform_field_value

    sub = frame.select(pl.col("__row_id__").cast(pl.Int64), pl.col(f.field))
    rids = sub["__row_id__"].to_numpy()
    raws = sub[f.field].to_list()
    tmap = {v: _transform_field_value(v, f) for v in set(raws)}
    value_table: list = [None]  # code 0 == None (unobserved)
    code_of: dict[Any, int] = {None: 0}
    codes_by_rowid = np.zeros(n_rows, dtype=np.int32)
    row_codes = np.empty(len(rids), dtype=np.int32)
    for i, raw in enumerate(raws):
        tv = tmap[raw]
        c = code_of.get(tv, -1)
        if c == -1:
            c = len(value_table)
            value_table.append(tv)
            code_of[tv] = c
        row_codes[i] = c
    codes_by_rowid[rids] = row_codes
    return codes_by_rowid, value_table, 0


def _paired_similarity_codes(
    ca: np.ndarray, cb: np.ndarray, value_table: list, scorer_name: str
) -> np.ndarray:
    """Per-pair similarity via a value-pair dedup over CODE pairs: score each
    distinct ``(code_a, code_b)`` once and gather. Null (code 0) scores 0.0."""
    from goldenmatch.backends.score_buckets import _resolve_score_pair_callable

    scorer_fn = _resolve_score_pair_callable(scorer_name, None)
    k = np.int64(len(value_table))
    pk = ca.astype(np.int64) * k + cb.astype(np.int64)
    uniq, inv = np.unique(pk, return_inverse=True)
    ua = (uniq // k).astype(np.int64)
    ub = (uniq % k).astype(np.int64)
    sims = np.empty(uniq.shape[0], dtype=np.float64)
    for i in range(uniq.shape[0]):
        xa = int(ua[i])
        xb = int(ub[i])
        if xa == 0 or xb == 0:
            sims[i] = 0.0
        else:
            va = value_table[xa]
            vb = value_table[xb]
            sims[i] = 0.0 if (va is None or vb is None) else float(scorer_fn(va, vb))
    return sims[inv]


def score_fs_pair_dedup(
    score_frame,
    blocking: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set,
    em_result,
) -> list[tuple[int, int, float]] | None:
    """Vectorised distinct-candidate-pair FS scorer. Returns ``(a, b, score)`` for
    pairs at/above ``mk``'s (review-cut) link threshold, excluding
    ``matched_pairs``. Returns ``None`` when the candidate set exceeds the memory
    cap — the caller then falls back to the bucket route.

    ``mk`` is the dispatch's ``scoring_mk`` (its ``link_threshold`` is already the
    review floor), so this emits the SAME review-inclusive set ``score_buckets``
    does; the caller splits link/review via ``_split_probabilistic_pairs``."""
    from goldenmatch.core.frame import to_frame
    from goldenmatch.core.probabilistic import (
        _fs_calibration_mode,
        _fs_link_threshold,
        _fs_require_positive_evidence,
        _levels_from_similarity,
        posterior_from_weight,
        prior_weight,
    )

    fobj = to_frame(score_frame)
    frame = fobj.native
    n_rows = int(fobj.height)
    got = _distinct_candidate_pairs(
        frame, blocking,
        skip_oversized=blocking.skip_oversized,
        max_block_size=blocking.max_block_size,
        cap=_pair_count_cap(),
    )
    if got is None:
        return None  # over the memory cap -> bucket-route fallback
    a, b = got
    p = a.shape[0]
    if p == 0:
        return []

    calibrated = _fs_calibration_mode() == "posterior"
    prior_w = prior_weight(em_result.proportion_matched) if calibrated else 0.0
    link_threshold = _fs_link_threshold(mk, em_result, calibrated)

    total_weight = np.zeros(p, dtype=np.float64)
    has_evidence = np.zeros(p, dtype=bool)
    pair_min = np.zeros(p, dtype=np.float64)
    pair_max = np.zeros(p, dtype=np.float64)

    for f in mk.fields:
        codes_by_rowid, value_table, null_code = _field_codes(frame, f, n_rows)
        ca = codes_by_rowid[a]
        cb = codes_by_rowid[b]
        weights = np.asarray(em_result.match_weights[f.field], dtype=np.float64)
        sim = _paired_similarity_codes(ca, cb, value_table, f.scorer)
        lvl = _levels_from_similarity(
            sim, int(f.levels), float(f.partial_threshold),
            level_thresholds=f.level_thresholds,
        )
        observed = (ca != null_code) & (cb != null_code)
        has_evidence |= observed
        total_weight += np.where(observed, weights[lvl], 0.0)
        pair_min += float(weights.min())
        pair_max += float(weights.max())

    if calibrated:
        normalized = posterior_from_weight(total_weight, prior_w)
    else:
        pair_range = pair_max - pair_min
        normalized = np.full(p, 0.5, dtype=np.float64)
        np.divide(total_weight - pair_min, pair_range, out=normalized, where=pair_range > 0)
        normalized = np.clip(normalized, 0.0, 1.0)
        normalized = np.where(~has_evidence & (total_weight == 0.0), 0.5, normalized)
        if _fs_require_positive_evidence():
            normalized = np.where(total_weight <= 0.0, -1.0, normalized)

    keep = normalized >= link_threshold
    ak = a[keep].tolist()
    bk = b[keep].tolist()
    sk = np.round(normalized[keep], 4).tolist()
    out: list[tuple[int, int, float]] = []
    for x, y, s in zip(ak, bk, sk):
        if (x, y) in matched_pairs:  # a,b are already (lo, hi) canonical
            continue
        out.append((x, y, s))
    return out
