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

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig, MatchkeyConfig
from goldenmatch.core._native_loader import native_enabled, native_module
from goldenmatch.core.bench import record_metrics, stage

logger = logging.getLogger(__name__)


def _bkt_debug_on() -> bool:
    """Whether the verbose `[score_buckets]` diagnostics print. OFF by default:
    now that bucket is the DEFAULT scorer, unconditional prints to stdout would
    corrupt any tool that parses a dedupe's stdout (e.g. the frame-diff harness's
    JSON subprocess). Set GOLDENMATCH_BUCKET_DEBUG=1 to re-enable."""
    return os.environ.get("GOLDENMATCH_BUCKET_DEBUG", "0") not in (
        "0", "", "false", "False", "no", "off",
    )


def _fs_bucket_native_enabled() -> bool:
    """Whether the batched native FS bucket scorer is active (default ON).

    ``GOLDENMATCH_FS_BUCKET_NATIVE=0`` forces the per-block ``prob_scorer`` loop
    inside ``_score_one_bucket`` (the parity escape hatch — byte-identical to the
    per-block native path). Only gates the BATCHED bucket call; whether the
    per-block loop itself is native still follows ``_fs_native_eligible`` /
    ``GOLDENMATCH_FS_NATIVE``."""
    return os.environ.get("GOLDENMATCH_FS_BUCKET_NATIVE", "1").strip().lower() not in (
        "0", "false", "no", "off", "disabled",
    )


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


# Scorers whose batched NxN matrix form is BYTE-IDENTICAL to the per-pair
# score_pair callable (asserted scorer-by-scorer in
# tests/test_score_buckets_vectorized_fallback.py). The vectorized fast-path
# lane (_score_block_vec) only fires for a field whose scorer is in this set.
#
# float64 throughout. _fuzzy_score_matrix casts its matrices to float32 for the
# 1M-row memory budget, but the per-pair fast-path loop accumulates in float64,
# so a float32 matrix would flip borderline >= threshold decisions (the exact
# "per-pair reimpl silently diverges" failure mode the ensemble decline in
# _resolve_score_pair_callable warns about). The lane is size-capped
# (GOLDENMATCH_BUCKET_VEC_MAX, default 2000) precisely so the float64 NxN stays
# cheap and we never have to trade bits for memory.
# NOTE: dice / jaccard are deliberately EXCLUDED. Their _dice_score_matrix /
# _jaccard_score_matrix are the PPRL bloom-filter (hex-CLK) scorers -- a
# different computation from the per-pair _dice_score_single / _jaccard_score_single
# bigram coefficients, so the matrix is NOT byte-parity (it raises on plain
# strings). The parity test catches this; do not add them back without a matrix
# form that matches the per-pair callable bit-for-bit.
_VEC_SUPPORTED: frozenset[str] = frozenset(
    {"soundex_match", "jaro_winkler", "levenshtein", "token_sort", "ensemble"}
)


def _vec_field_matrix(values: list, scorer_name: str):
    """float64 NxN score matrix for ``scorer_name`` over ``values``.

    matrix[i, j] is byte-identical to the per-pair score_pair(values[i],
    values[j]) -- same rapidfuzz / jellyfish primitive, just batched. Only the
    scorers in ``_VEC_SUPPORTED`` reach here.
    """
    import numpy as np
    from rapidfuzz.distance import JaroWinkler, Levenshtein
    from rapidfuzz.fuzz import token_sort_ratio
    from rapidfuzz.process import cdist

    if scorer_name == "soundex_match":
        from goldenmatch.core.scorer import _soundex_score_matrix
        return _soundex_score_matrix(values).astype(np.float64, copy=False)
    if scorer_name == "dice":
        from goldenmatch.core.scorer import _dice_score_matrix
        return _dice_score_matrix(values).astype(np.float64, copy=False)
    if scorer_name == "jaccard":
        from goldenmatch.core.scorer import _jaccard_score_matrix
        return _jaccard_score_matrix(values).astype(np.float64, copy=False)
    if scorer_name == "ensemble":
        # float64 max(jaro_winkler, token_sort/100, soundex*0.8) -- byte-identical
        # to the per-pair `_ensemble_score_single` (same three components, same 0.8
        # bonus, all float64). The soundex bonus reuses `_soundex_score_matrix`,
        # the SAME matrix the per-pair soundex_match callable is byte-parity with
        # (asserted in tests/test_score_buckets_vectorized_fallback.py), so the
        # ensemble vec form agrees with `_ensemble_score_single`'s canonical
        # soundex empty-code-guard semantics (garbage/empty never matches).
        from goldenmatch.core.scorer import _soundex_score_matrix
        jw = np.asarray(cdist(values, values, scorer=JaroWinkler.similarity, dtype=np.float64))
        ts = np.asarray(cdist(values, values, scorer=token_sort_ratio, dtype=np.float64)) / 100.0
        sx = _soundex_score_matrix(values).astype(np.float64) * 0.8
        return np.maximum(np.maximum(jw, ts), sx)
    if scorer_name == "jaro_winkler":
        return np.asarray(cdist(values, values, scorer=JaroWinkler.similarity, dtype=np.float64))
    if scorer_name == "levenshtein":
        return np.asarray(
            cdist(values, values, scorer=Levenshtein.normalized_similarity, dtype=np.float64)
        )
    # token_sort: rapidfuzz returns 0-100; per-pair divides by 100.0 (same op order).
    return np.asarray(cdist(values, values, scorer=token_sort_ratio, dtype=np.float64)) / 100.0


def _score_block_vec(
    row_ids: list,
    field_arrays: list,
    scorer_names: list,
    weights: list,
    offset: int,
    end: int,
    total_weight: float,
    threshold: float,
    frozen_exclude: frozenset,
) -> list:
    """Score one block via batched matrices instead of the Python per-pair loop.

    Byte-parity with _score_one_bucket_fast's per-pair branch: combines fields
    in the same order (sum of matrix*weight, divided by total_weight), emits
    canonical (min, max) pairs >= threshold in row-major (i<j) order with
    exclusions removed. The O(n**2) work (scoring + threshold scan) is numpy;
    only the emitted pairs (few, >= threshold) touch Python.
    """
    import numpy as np

    n = end - offset
    num = None
    wsum = None
    for f_idx, name in enumerate(scorer_names):
        vals = field_arrays[f_idx][offset:end]
        m = _vec_field_matrix(vals, name)
        contrib = m * weights[f_idx]
        num = contrib if num is None else num + contrib
        # #weighted-null: a null field is ABSENCE of evidence, not disagreement,
        # so it must leave the DENOMINATOR too -- otherwise an absolute threshold
        # becomes unreachable (0.3/0.4/0.3 fields @0.85: a null dob caps the pair
        # at 0.70 however perfectly the names agree). _vec_field_matrix collapses
        # a null pair to 0.0 -- indistinguishable from a genuine 0.0 score -- so
        # the mask is rebuilt from the raw values here. Mirrors
        # native/src/score.rs and core/scorer.py::score_pair.
        null = np.array([v is None for v in vals], dtype=bool)
        obs = (~(null[:, None] | null[None, :])) * weights[f_idx]
        wsum = obs if wsum is None else wsum + obs
    with np.errstate(invalid="ignore", divide="ignore"):
        combined = np.where(wsum > 0.0, num / np.where(wsum > 0.0, wsum, 1.0), 0.0)
    iu0, iu1 = np.triu_indices(n, k=1)
    flat = combined[iu0, iu1]
    sel = flat >= threshold
    if not bool(sel.any()):
        return []
    ids = row_ids[offset:end]
    out: list[tuple[int, int, float]] = []
    for a_idx, b_idx, s in zip(iu0[sel].tolist(), iu1[sel].tolist(), flat[sel].tolist()):
        ri = ids[a_idx]
        rj = ids[b_idx]
        pair_key = (ri, rj) if ri < rj else (rj, ri)
        if frozen_exclude and pair_key in frozen_exclude:
            continue
        out.append((pair_key[0], pair_key[1], s))
    return out


# Scorers the native fast-path kernel (goldenmatch._native.score_block_pairs)
# implements, with the ids it expects. A field whose scorer isn't here forces
# the Python per-pair loop for that bucket.
_NATIVE_SCORER_IDS: dict[str, int] = {
    "jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3,
    # id 4 = date (score-core score_one). Routing to native is GUARDED on the
    # `date_similarity` capability symbol at the gating site below: a stale
    # published wheel (pre-date) would hit score_one's catch-all and silently
    # score every date pair 0.0, so absent the symbol, date declines to the
    # pure-Python mirror. NOTE: distinct from _NATIVE_FIELD_SCORER_IDS (the
    # score_field_matrix path), where id 4 is soundex_match -- different kernel,
    # different namespace; date is not wired into that path.
    "date": 4,
    # ids 17/18 = date_diff / geo_haversine (FS domain comparators, score-core
    # score_one, spec 2026-07-23). (ids 15/16 below are the name scorers.) Same
    # wheel-skew story as date: routing is GUARDED on the `date_diff_similarity` /
    # `geo_haversine_similarity` capability symbols at the gating site below, so a
    # stale wheel (whose score_one catch-all scores ids 17/18 as 0.0) declines to
    # the pure-Python per-pair mirrors (`_date_diff_similarity_py` /
    # `_geo_haversine_similarity_py`). Only the PARAMETERLESS comparators are
    # kernel-backed; `numeric_diff` carries its band on the scorer string, which
    # the fixed-id score_one(id,a,b) can't convey.
    "date_diff": 17,
    "geo_haversine": 18,
    # id 5 = qgram (char-trigram Jaccard, score-core score_one). Same wheel-skew
    # story as date: routing is GUARDED on the `qgram_similarity` capability
    # symbol at the gating site below, so a stale wheel (pre-qgram, whose
    # score_one catch-all scores id 5 as 0.0) declines to the pure-Python
    # per-pair mirror (`_qgram_score_single`) instead of silently zeroing.
    "qgram": 5,
    # id 6 = soundex_match (binary soundex-code equality, score-core score_one).
    # Same wheel-skew story: guarded on the `soundex_similarity` capability symbol
    # so a stale wheel declines to the pure-Python per-pair jellyfish mirror. NB
    # score_one id 6 uses NAIVE code equality (two empty codes -> match), matching
    # `_resolve_score_pair_callable`'s `jf.soundex(a)==jf.soundex(b)` bucket mirror
    # -- NOT the field-matrix path's id 4=soundex (empty codes non-matching;
    # a separate id namespace).
    "soundex_match": 6,
    # id 7 = initialism_match (abbreviation matcher, score-core score_one).
    # Two-part wheel-skew guard at the gating site below: routing to native id 7
    # requires BOTH the `initialism_similarity` capability symbol (a stale
    # pre-initialism wheel hits score_one's catch-all -> silent 0.0) AND a
    # successful `set_legal_form_variants` install (id 7 scores against an empty
    # legal-form set until the host ships `entity_form_variants()`), else it
    # declines to the pure-Python per-pair mirror (`_initialism_match_single`).
    "initialism_match": 7,
    # id 8 = alias_match (business + given-name canonical equality, score-core
    # score_one). Two-part wheel-skew guard at the gating site below: routing to
    # native id 8 requires BOTH the `alias_match_similarity` capability symbol (a
    # stale pre-alias wheel hits score_one's catch-all -> silent 0.0) AND a
    # successful install of the business + given-name tables (id 8 scores against
    # empty tables until the host ships them), else it declines to the pure-Python
    # per-pair mirror (`_alias_match_single`).
    "alias_match": 8,
    # ids 9/10/11 = dice / jaccard / phash (bloom-hex + hex-hamming, score-core
    # score_one). Byte-exact with the per-pair `_dice_score_single` /
    # `_jaccard_score_single` / `_phash_score_single` (integer popcount + one f64
    # divide -- the numpy MATRIX forms are float32, a separate path). Guarded on
    # the `dice_similarity` / `jaccard_similarity` / `phash_similarity` capability
    # symbols so a stale wheel declines to those pure mirrors. dice/jaccard are
    # padding-invariant; phash matches the PAIRWISE `_phash_score_single` (Option A
    # in docs/superpowers/specs/2026-07-21-block-aware-bucket-kernel-design.md),
    # NOT the block-global `_phash_score_matrix`.
    "dice": 9,
    "jaccard": 10,
    "phash": 11,
    # id 12 = ensemble: max(jaro_winkler, unscaled token_sort, 0.8*soundex),
    # composing score_one 0/2/6 (score-core `ensemble_similarity`). Matches the
    # per-pair `_ensemble_score_single` to machine epsilon (1.1e-16 on real
    # Febrl3 name/address pairs). Guarded on the `ensemble_similarity` capability
    # symbol so a stale wheel (pre-ensemble score_one) declines to the pure
    # per-pair mirror instead of silently scoring the whole matchkey 0.0.
    "ensemble": 12,
    # ids 13/14 = radial / audio_fp (perceptual profile scorers: score-core
    # score_one). Byte-exact with the per-pair `_radial_score_single` /
    # `_audio_fp_score_single` (hex-parse + alignment search + f64 reductions --
    # the numpy MATRIX forms are a symmetric pairwise loop, same math). Guarded on
    # the `radial_similarity` / `audio_fp_similarity` capability symbols so a stale
    # wheel (pre-radial/audio score_one) declines to those pure mirrors instead of
    # silently zeroing the id via score_one's catch-all.
    "radial": 13,
    "audio_fp": 14,
    # ids 15/16 = name_freq_weighted_jw / given_name_aliased_jw (the census-IDF /
    # given-name-alias name scorers). UNLIKE ids 0..=14 these are NOT score_one
    # scorers -- score_one is stateless and cannot reach reference tables. The
    # WEIGHTED bucket kernel intercepts 15/16 and dispatches them through
    # fs-core's `name_freq_weighted_sim` / `given_name_aliased_sim` over the
    # process-global census/alias tables (`set_name_reference_data`). Two-part
    # wheel-skew guard at the gating site: routing requires BOTH the
    # `NATIVE_SUPPORTS_NAME_BUCKET_SCORERS` capability flag AND a successful table
    # install (`_ensure_name_tables_installed`), folded into one memoized bool; a
    # stale wheel (score_one catch-all -> 0.0) declines to the pure plugin path.
    # ADDITIONALLY name_freq_weighted_jw declines native when its field carries a
    # per-dataset `tf_freqs` table (#1207 default-on): fs-core ports only the
    # STATIC-census branch, so a tf field stays on the vectorized find_fuzzy_matches
    # (which applies the tf downweight via the plugin matrix path). Parity is
    # tolerance-bounded, not byte-exact: the native base JW is rapidfuzz-rs vs the
    # plugin's rapidfuzz-py -- native is the reference (see the FS name-scorer
    # story in probabilistic.py + tests/test_native_name_scorer_parity.py).
    "name_freq_weighted_jw": 15,
    "given_name_aliased_jw": 16,
}


# Cached tri-state for the one-time legal-form install into the native kernel:
# None = not yet attempted, True = installed on a capable kernel, False = kernel
# lacks the symbols (stale wheel) so the initialism native route is declined.
_LEGAL_FORMS_INSTALLED: bool | None = None


def _ensure_legal_forms_installed() -> bool:
    """Install ``entity_form_variants()`` into score-core's process-global
    legal-form table (via the native ``set_legal_form_variants`` shim) exactly
    once, so ``score_one`` id 7 (initialism_match) drops legal forms the same way
    the pure ``derive_initialism`` does.

    Returns True once the table is installed on a kernel exposing BOTH
    ``set_legal_form_variants`` and ``initialism_similarity``; False when the
    loaded kernel lacks either symbol (a stale pre-initialism wheel), so the
    gating site declines the native id-7 route and lets the pure per-pair mirror
    (``_initialism_match_single``) score the block. Idempotent + memoized: the
    OnceLock is first-wins and the variant set is deterministic, so a benign
    re-install (another caller won the race) still leaves the correct table.
    """
    global _LEGAL_FORMS_INSTALLED
    if _LEGAL_FORMS_INSTALLED is not None:
        return _LEGAL_FORMS_INSTALLED
    _mod = native_module()
    if (
        _mod is None
        or not hasattr(_mod, "set_legal_form_variants")
        or not hasattr(_mod, "initialism_similarity")
    ):
        _LEGAL_FORMS_INSTALLED = False
        return False
    try:
        from goldenmatch.refdata.business import entity_form_variants

        # Bool return (first-wins) is intentionally ignored: whether we or another
        # caller installed it, the deterministic content is now in place.
        _mod.set_legal_form_variants(list(entity_form_variants()))
        _LEGAL_FORMS_INSTALLED = True
    except Exception:
        _LEGAL_FORMS_INSTALLED = False
    return _LEGAL_FORMS_INSTALLED


# Same tri-state as _LEGAL_FORMS_INSTALLED, for the alias_match business +
# given-name canonical tables.
_ALIAS_TABLES_INSTALLED: bool | None = None


def _ensure_alias_tables_installed() -> bool:
    """Install the business + given-name canonical tables into score-core's
    process-global state (via the native ``set_business_aliases`` /
    ``set_given_name_canonicals`` shims) exactly once, so ``score_one`` id 8
    (alias_match) canonicalizes the same way the pure ``_alias_match_single`` does.

    Ships:
      * business ``strip_legal_form`` variants (``business._state.variants_normalized``)
        -- rebuilt kernel-side into the trailing-suffix regex -- plus the raw
        ``surface_to_canonical`` alias map.
      * a PRE-RESOLVED given-name ``normalized -> min(canonical set)`` map, so the
        kernel needs no alias graph, only a normalize + lookup.

    Returns True once installed on a kernel exposing ``set_business_aliases``,
    ``set_given_name_canonicals`` AND ``alias_match_similarity``; False when the
    loaded kernel lacks a symbol (a stale pre-alias wheel) or the refdata packs
    are unavailable, so the gating site declines the native id-8 route and lets
    the pure ``_alias_match_single`` mirror score the block. Idempotent +
    memoized; the OnceLocks are first-wins and the tables are deterministic.
    """
    global _ALIAS_TABLES_INSTALLED
    if _ALIAS_TABLES_INSTALLED is not None:
        return _ALIAS_TABLES_INSTALLED
    _mod = native_module()
    if (
        _mod is None
        or not hasattr(_mod, "set_business_aliases")
        or not hasattr(_mod, "set_given_name_canonicals")
        or not hasattr(_mod, "alias_match_similarity")
    ):
        _ALIAS_TABLES_INSTALLED = False
        return False
    try:
        from goldenmatch.refdata import business as _business
        from goldenmatch.refdata import business_aliases as _ba
        from goldenmatch.refdata import given_names as _gn

        _business._load()
        _ba._load()
        _gn._load()
        # Refdata packs must be present for the kernel to canonicalize; without
        # them the pure mirror is the only faithful path.
        if _business._state is None or _ba._state is None or _gn._state is None:
            _ALIAS_TABLES_INSTALLED = False
            return False
        # Bool returns (first-wins) intentionally ignored: deterministic content.
        _mod.set_business_aliases(
            list(_business._state.variants_normalized),
            list(_ba._state.surface_to_canonical.items()),
        )
        _mod.set_given_name_canonicals(
            [(k, min(v)) for k, v in _gn._state.canonicals.items()]
        )
        _ALIAS_TABLES_INSTALLED = True
    except Exception:
        _ALIAS_TABLES_INSTALLED = False
    return _ALIAS_TABLES_INSTALLED


# Same tri-state as the two above, for the census surname-IDF + given-name alias
# tables the weighted-bucket name scorers (ids 15/16) dispatch over.
_NAME_TABLES_INSTALLED: bool | None = None


def _ensure_name_tables_installed() -> bool:
    """Install the census surname-IDF + given-name alias tables into the native
    kernel's process-global ``NameRefData`` (via ``set_name_reference_data``)
    exactly once, so the WEIGHTED bucket ids 15/16 (``name_freq_weighted_jw`` /
    ``given_name_aliased_jw``) score over the SAME tables the pure plugin scorers
    do (``refdata.scorer.NameFreqWeightedJW`` / ``GivenNameAliasedJW``).

    Shares the process-global with the FS path's ``_ensure_fs_name_refdata`` (one
    kernel table), so a benign double-install is skipped via
    ``has_name_reference_data``.

    Returns True once installed on a kernel exposing ``set_name_reference_data``
    AND the ``NATIVE_SUPPORTS_NAME_BUCKET_SCORERS`` capability flag; False when the
    loaded kernel lacks a symbol (a stale wheel whose ``score_one`` catch-all would
    score ids 15/16 as 0.0) or the census/alias packs are unavailable, so the
    gating site declines the native route and the pure per-pair plugin mirror (or,
    at the fast-path guard, the vectorized ``find_fuzzy_matches``) scores the block.
    Idempotent + memoized; the reference tables are deterministic.
    """
    global _NAME_TABLES_INSTALLED
    if _NAME_TABLES_INSTALLED is not None:
        return _NAME_TABLES_INSTALLED
    _mod = native_module()
    if (
        _mod is None
        or not hasattr(_mod, "set_name_reference_data")
        or not hasattr(_mod, "NATIVE_SUPPORTS_NAME_BUCKET_SCORERS")
    ):
        _NAME_TABLES_INSTALLED = False
        return False
    try:
        from goldenmatch.refdata.given_names import export_alias_forms
        from goldenmatch.refdata.surnames import export_counts

        surname_counts = [(n, float(c)) for n, c in export_counts()]
        alias_forms = export_alias_forms()
        # Both packs empty -> the kernel would score every name pair as plain JW
        # (name_freq_weighted degrades to JW with no census table; given_name_-
        # aliased to JW with no alias graph). The pure plugin path is the only
        # faithful route in that case, so decline.
        if not surname_counts and not alias_forms:
            _NAME_TABLES_INSTALLED = False
            return False
        # Skip a redundant install if the FS path already registered the SAME
        # deterministic tables into the shared process-global.
        if not (
            hasattr(_mod, "has_name_reference_data") and _mod.has_name_reference_data()
        ):
            _mod.set_name_reference_data(surname_counts, alias_forms)
        _NAME_TABLES_INSTALLED = True
    except Exception:
        _NAME_TABLES_INSTALLED = False
    return _NAME_TABLES_INSTALLED


# Single source in core._hashing (re-exported here for back-compat). The
# distributed record store imports the SAME constant, so bucket assignment is
# identical across the two surfaces. Was a duplicated literal; drift is now
# gated by test_cross_surface_consistency.
from goldenmatch.core._hashing import BUCKET_HASH_SEED  # noqa: E402


def _fs_bounded_stream_enabled() -> bool:
    """Whether the FS bucket route scores buckets by BOUNDED STREAMING (slice one
    bucket off the keyed frame on demand) instead of the eager
    ``partition_by``-into-all-``n_buckets`` materialization.

    Motivation (``docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md``):
    the eager partition holds ``n_buckets`` frames (~1x the frame) live through the
    whole ``bucket_score`` loop, on TOP of the transient double at ``partition_by``
    time -- the dominant remaining single-node FS peak at >=1M after the EM
    ``build_blocks`` fix. Streaming holds only the keyed frame + ``max_workers``
    in-flight bucket slices (the spec's in-RAM ``FrameBlockSource``).

    ``GOLDENMATCH_FS_BLOCK_SOURCE=frame`` opts in; default (unset / any other value)
    keeps the byte-identical eager path until the >=1M peak/wall win is CI-measured
    and the default is flipped. Scoped to the FS (probabilistic) bucket route; the
    weighted path is untouched. The DuckDB (above-RAM) source is a follow-on
    (``score_fs_out_of_core`` already covers the out-of-core FS scoring lane)."""
    return os.environ.get("GOLDENMATCH_FS_BLOCK_SOURCE", "").strip().lower() == "frame"


def _default_n_buckets(height: int | None = None) -> int:
    """CPU-derived floor, data-scaled above it (#1803 item 5).

    Bucket count tracks rows upward (target ~50K rows/bucket, capped at 4096
    to bound partition bookkeeping) so per-bucket frame size stays bounded at
    10M+ instead of growing linearly under the old CPU-only cap. Output pairs
    are invariant to the bucket count (blocks hash wholly into one bucket);
    only the partition granularity changes. ``height=None`` keeps the legacy
    CPU-only formula for callers without a frame in hand.
    """
    base = min((os.cpu_count() or 4) * 4, 1024)
    if height is None:
        return base
    return min(max(base, height // 50_000), 4096)


def _resolve_score_pair_callable(
    scorer_name: str, tf_freqs: dict[str, float] | None = None
) -> Any:
    """Return a (str_a, str_b) -> float | None callable for a scorer name.

    Used by the bucket scorer's fast path so per-pair work skips the
    PluginRegistry / dispatch overhead that ``_fuzzy_score_matrix`` does
    per (block x field). None when the scorer isn't fast-path safe
    (embedding, ensemble, record_embedding, unknown).

    ``tf_freqs`` (#1781): the field's per-dataset value-frequency table
    (``MatchkeyField.tf_freqs``). Only the PLUGIN branch consumes it --
    built-in scorers ignore frequency tables, mirroring the legacy path
    where the table travels only through the plugin protocol. When set,
    the returned callable binds it as ``fn(a, b, tf_freqs=...)`` so the
    fast path matches the legacy matrix path (core/scorer.py:1236);
    pre-fix the table was silently dropped here, which is why the
    sample-time controller telemetry showed the downweight biting while
    the final dedupe output was byte-identical with it OFF.
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
        # GoldenMatch canonical soundex (byte-matches score-core); per-pair binary
        # match with the empty-code guard (garbage/empty never matches). Identical
        # to the matrix path's soundex_match, just one call at a time.
        from goldenmatch.core.scorer import _soundex_score_single
        return _soundex_score_single
    if scorer_name == "date":
        # Date-aware scorer (#1858). Per-pair mirror of score-core::date_similarity
        # (native id 4); byte-identical to the kernel (native-parity asserted).
        from goldenmatch.core.scorer import _date_similarity_py
        return _date_similarity_py
    if scorer_name == "date_diff":
        # Magnitude-aware date comparator (spec 2026-07-23). Per-pair mirror of
        # score-core::date_diff_similarity (native id 15); parity-asserted in
        # tests/test_native_date_diff_geo_parity.py. Making it fast-path eligible
        # routes date_diff configs through the bucket backend (native id 15 or
        # this per-pair mirror) instead of the slow matrix path.
        from goldenmatch.core.scorer import _date_diff_similarity_py
        return _date_diff_similarity_py
    if scorer_name == "geo_haversine":
        # Great-circle comparator (spec 2026-07-23). Per-pair mirror of
        # score-core::geo_haversine_similarity (native id 16); parity-asserted in
        # tests/test_native_date_diff_geo_parity.py.
        from goldenmatch.core.scorer import _geo_haversine_similarity_py
        return _geo_haversine_similarity_py
    if scorer_name == "qgram":
        # Character-trigram Jaccard (n=3). Per-pair mirror of the matrix path
        # (_qgram_score_matrix) AND of score-core::qgram_similarity (native
        # id 5); the three are parity-asserted in tests/test_native_qgram_parity.py.
        # Making qgram fast-path eligible routes qgram configs through the bucket
        # backend (native or this per-pair mirror) instead of the slow matrix path.
        from goldenmatch.core.scorer import _qgram_score_single
        return _qgram_score_single
    if scorer_name == "initialism_match":
        # Abbreviation matcher (1.0/0.0). Per-pair mirror of the matrix path
        # (core/scorer.py:736) AND of score-core::initialism_match (native id 7);
        # parity-asserted in tests/test_native_initialism_parity.py. Making it
        # fast-path eligible routes initialism_match configs through the bucket
        # backend (native id 7 or this per-pair mirror) instead of the slow
        # matrix path. The native id-7 kernel needs the legal-form table
        # installed (`set_legal_form_variants`); the gating site below guards on
        # both the `initialism_similarity` symbol AND a successful install, and
        # declines to THIS mirror otherwise.
        from goldenmatch.core.scorer import _initialism_match_single
        return _initialism_match_single
    if scorer_name == "alias_match":
        # Business + given-name canonical equality (1.0/0.0). Per-pair mirror of
        # the matrix path (core/scorer.py:_alias_score_matrix) AND of
        # score-core::alias_match (native id 8); parity-asserted in
        # tests/test_native_alias_parity.py. Making it fast-path eligible routes
        # alias_match configs through the bucket backend (native id 8 or this
        # per-pair mirror) instead of the slow matrix path. The native id-8 kernel
        # needs the business + given-name tables installed
        # (`set_business_aliases`/`set_given_name_canonicals`); the gating site
        # below guards on both the `alias_match_similarity` symbol AND a successful
        # install, and declines to THIS mirror otherwise.
        from goldenmatch.core.scorer import _alias_match_single
        return _alias_match_single
    if scorer_name == "dice":
        # Bloom-hex Dice coefficient (2*|A&B|/(|A|+|B|)). Per-pair mirror of
        # score-core::dice_similarity (native id 9); parity-asserted in
        # tests/test_native_bloom_hash_parity.py. Integer popcount, so it's
        # byte-exact with the kernel (the numpy _dice_score_matrix is float32, a
        # separate path). The gating site guards on the `dice_similarity` symbol
        # and declines to THIS mirror otherwise.
        from goldenmatch.core.scorer import _dice_score_single
        return _dice_score_single
    if scorer_name == "jaccard":
        # Bloom-hex Jaccard (|A&B|/|A|B|). Per-pair mirror of
        # score-core::jaccard_similarity (native id 10); same integer-popcount
        # byte-parity story as dice.
        from goldenmatch.core.scorer import _jaccard_score_single
        return _jaccard_score_single
    if scorer_name == "phash":
        # Perceptual-hash Hamming similarity (1 - dist/nbits). Per-pair mirror of
        # score-core::phash_similarity (native id 11). NOTE: this makes phash
        # fast-path eligible for the FIRST time -- it previously declined to the
        # slow `find_fuzzy_matches` MATRIX path (`_phash_score_matrix`, float32 +
        # block-GLOBAL max-length padding). The bucket path uses this PAIRWISE
        # float64 `_phash_score_single` instead (Option A in
        # docs/superpowers/specs/2026-07-21-block-aware-bucket-kernel-design.md):
        # byte-identical for fixed-length pHashes (the normal 64-bit case), and a
        # precision improvement (float64) elsewhere. Guarded on `phash_similarity`.
        from goldenmatch.core.scorer import _phash_score_single
        return _phash_score_single
    if scorer_name == "ensemble":
        # ensemble is fast-path eligible by DEFAULT (2026-07-21, re-enabled after
        # the perf guard below made it safe). It rides the float64 bucket fast
        # path: `_ensemble_score_single` per-pair (tiny blocks) + the
        # byte-identical `_vec_field_matrix('ensemble')` vectorized lane (mid
        # blocks; ensemble is in `_VEC_SUPPORTED`) + the native `score_one` id 12
        # arrow kernel on all-native matchkeys. Measured 1.47x faster end-to-end
        # on Febrl3, byte-identical recall.
        #
        # The earlier default-on attempt (#1995) hung a CI worker: making ensemble
        # resolvable flipped a NAME-SCORER matchkey (name_freq_weighted_jw /
        # given_name_aliased_jw -- neither vec-supported nor native-id) onto the
        # fast path, where those fields ran the O(N^2) per-pair Python loop, 19x
        # slower than the vectorized find_fuzzy_matches (26.97s vs 1.40s on the
        # same 65 NCVR blocks), blowing the per-test timeout. That is fixed at the
        # SOURCE by the PERF GUARD in `_resolve_fast_path` (declines the fast path
        # whenever a field would force per-pair Python) -- so an ensemble field can
        # no longer drag a per-pair-Python matchkey onto the slow path. See
        # docs/superpowers/specs/2026-07-21-ensemble-kernel-measurement.md.
        #
        # KILL-SWITCH: `GOLDENMATCH_ENSEMBLE_KERNEL=0` (or `off`/`false`) restores
        # the historical decline (float32 find_fuzzy_matches) for rollback /
        # byte-for-byte reproduction of pre-2026-07-21 output.
        mode = os.environ.get("GOLDENMATCH_ENSEMBLE_KERNEL", "").strip().lower()
        if mode in ("0", "off", "false", "no"):
            return None
        from goldenmatch.core.scorer import _ensemble_score_single
        return _ensemble_score_single
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
    if fn is None or not tf_freqs:
        return fn  # may itself be None for matrix-only plugins
    # #1781: bind the field's TF table so the fast path matches the legacy
    # matrix path (core/scorer.py:1236). Without this, the sample-time
    # controller telemetry shows the downweight biting (samples run legacy)
    # while the final dedupe silently drops it. TypeError fallback = the
    # score_pair-side twin of _fuzzy_score_matrix's score_matrix posture
    # (core/scorer.py:594-597): a legacy plugin without the keyword degrades
    # to the bare call. Per-call try is fine -- zero cost on the happy path.

    def _with_tf(a, b, _fn=fn, _tf=tf_freqs):
        try:
            return _fn(a, b, tf_freqs=_tf)
        except TypeError:
            return _fn(a, b)

    return _with_tf


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
        from goldenmatch.core.frame import to_frame as _tf_cols

        if xform_col not in _tf_cols(prepared_df).columns:
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
        if _bkt_debug_on():
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
        fn = _resolve_score_pair_callable(scorer, getattr(f, "tf_freqs", None))
        if fn is None:
            _decline(f"_resolve_score_pair_callable({scorer!r}) is None")
            return None
        xform_col = _xform_sig(f)
        from goldenmatch.core.frame import to_frame as _tf_cols

        if xform_col not in _tf_cols(prepared_df).columns:
            return None
        field_specs.append((xform_col, float(weight), fn, scorer))
        total_weight += float(weight)
    if total_weight <= 0:
        _decline(f"total_weight={total_weight}")
        return None
    # PERF GUARD (nested-pool / per-pair-Python fix, 2026-07-21): a field whose
    # scorer is NEITHER vec-supported (`_score_block_vec` / `_VEC_SUPPORTED`) NOR
    # native-kernel-backed (`_NATIVE_SCORER_IDS`) forces the O(N^2) per-pair
    # Python loop in `_score_one_bucket_fast`, which is DRAMATICALLY slower than
    # the vectorized `find_fuzzy_matches` (MEASURED 19x: 26.97s vs 1.40s on the
    # same 65 NCVR blocks for a matchkey with `name_freq_weighted_jw` /
    # `given_name_aliased_jw` -- neither vec nor native-id). On a slow CI runner
    # that 19x blows the per-test timeout -> the worker is os._exit'd. Decline to
    # `find_fuzzy_matches`, which vectorizes every scorer. All-native matchkeys
    # still ride the arrow kernel and all-vec matchkeys still ride the vec lane;
    # this only declines when a genuinely per-pair-Python field is present, where
    # the fast path was never actually fast. (This is what makes the ensemble
    # kernel safe to default-on: an ensemble field on a name-scorer matchkey no
    # longer drags the whole matchkey onto the slow per-pair path.)
    _fast_scorers = [s for _, _, _, s in field_specs]
    # The two name scorers are in `_NATIVE_SCORER_IDS` (ids 15/16), but they only
    # ACTUALLY get a kernel when the census/alias tables are installed on a capable
    # wheel -- and `name_freq_weighted_jw` additionally declines native when its
    # field carries a per-dataset `tf_freqs` table (#1207 default-on), since fs-core
    # ports only the static-census branch. In those cases the fast path would fall
    # to the O(N^2) per-pair Python loop (the 19x-slow regression the guard below
    # protects against), so treat them as per-pair-forcing -> decline to the
    # vectorized `find_fuzzy_matches`, which handles every scorer (incl. the tf
    # downweight via the plugin matrix path), exactly as before this change.
    _has_name_scorer = any(
        s in ("name_freq_weighted_jw", "given_name_aliased_jw") for s in _fast_scorers
    )
    _name_native_ok = _ensure_name_tables_installed() if _has_name_scorer else False
    _name_forces_per_pair: set[str] = set()
    if _has_name_scorer:
        for f in mk.fields:
            s = getattr(f, "scorer", None)
            if s == "name_freq_weighted_jw":
                if not _name_native_ok or getattr(f, "tf_freqs", None):
                    _name_forces_per_pair.add(s)
            elif s == "given_name_aliased_jw" and not _name_native_ok:
                _name_forces_per_pair.add(s)
    _per_pair_scorers = [
        s
        for s in _fast_scorers
        if (s not in _VEC_SUPPORTED and s not in _NATIVE_SCORER_IDS)
        or s in _name_forces_per_pair
    ]
    if _per_pair_scorers:
        _decline(
            f"scorer(s) {_per_pair_scorers} force the per-pair Python loop "
            f"(not vec, not native) -> find_fuzzy_matches is faster (perf guard)"
        )
        return None
    # NE PARITY GUARD: the slow path (_apply_negative_evidence) applies a penalty
    # for EVERY NE scorer `score_field` can score -- it only skips a scorer that
    # RAISES (cached in _NE_BROKEN). But the fast path can faithfully reproduce a
    # per-pair penalty only for scorers in _SCORE_FIELD_DIRECT_SCORERS;
    # _resolve_ne_specs silently DROPS the rest (ensemble / qgram / date / phash /
    # audio_fp / initialism_match / alias_match), zeroing a penalty the slow path
    # applies -> the SAME pair scores differently on bucket vs polars-direct.
    # Decline to the slow path whenever an NE scorer isn't fast-representable: for
    # a genuinely-broken scorer the slow path also skips it (identical result), so
    # declining is parity-correct either way. Parity over speed.
    for _ne in (mk.negative_evidence or []):
        _ne_scorer = getattr(_ne, "scorer", None)
        if _ne_scorer is not None and _ne_scorer not in _SCORE_FIELD_DIRECT_SCORERS:
            _decline(
                f"NE scorer {_ne_scorer!r} not in _SCORE_FIELD_DIRECT_SCORERS "
                f"(slow path would penalize; fast path can't) -> slow-path parity"
            )
            return None
    ne_specs = _resolve_ne_specs(mk, prepared_df)
    # Diagnostic on the success path: log matchkey shape so we can compare
    # what the controller commits at different row counts (rerank thresholds
    # and NE promotion are scale-dependent).
    if _bkt_debug_on():
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


def score_probabilistic_external_blocks(
    blocks: list,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    em_result,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int, float]]:
    """Memory-bounded Fellegi-Sunter scoring over EXTERNALLY-GENERATED blocks.

    ``score_buckets`` derives its own field-hash blocks, so candidate sets
    from the non-field blocking strategies (lsh / ann / learned / canopy /
    sorted_neighborhood) cannot route through it. This scorer consumes the
    strategy's own ``BlockResult`` list ONE BLOCK AT A TIME with the bucket
    lane's scale machinery: the frozen-exclude Arc handle built once
    (#552/#688), oversized auto-split honoring ``skip_oversized``
    (#1790/#1826), and the native (zero-copy arrow) / vectorized / scalar
    per-block scorer selection -- replacing
    ``score_probabilistic_blocks_batched``'s up-front all-units accumulation
    for these strategies (whose whole-mega-block dense scoring is the #1826
    OOM shape; a 388K-row canopy through the vectorized path is a 1.1 TiB
    dense-matrix allocation).

    Overlapping candidates (canopy membership, sorted-neighborhood windows)
    can surface the same pair from two blocks; a canonical-key seen set
    dedups in block order. Blocks carrying ``pre_scored_pairs`` (ann_pairs)
    score their frames exactly like the batched scorer does -- FS weights
    are not FAISS distances, so carried scores are ignored on this matchkey
    type there too.
    """
    from goldenmatch.core.blocker import _auto_split_block
    from goldenmatch.core.probabilistic import (
        _fs_native_eligible,
        probabilistic_block_scorer,
        score_probabilistic_bucket_native,
    )

    if em_result is None:
        raise ValueError(
            "score_probabilistic_external_blocks requires em_result for "
            "probabilistic scoring"
        )

    frozen_exclude = frozenset(matched_pairs)
    max_block_size = blocking_config.max_block_size
    skip_oversized = blocking_config.skip_oversized

    use_native = _fs_bucket_native_enabled() and _fs_native_eligible(mk)
    prob_scorer = None if use_native else probabilistic_block_scorer(mk, em_result)

    # The #552/#688 fix, FS side: build the Rust exclude set ONCE, not per
    # block call. Old wheels fall back to the Vec-per-call contract inside
    # _score_fs_native_frame (byte-identical, just slower).
    exclude_handle = None
    if use_native and frozen_exclude:
        try:
            _mod = native_module()
            _build = getattr(_mod, "build_exclude_set", None)
            if _build is not None and (
                getattr(_mod, "FS_SUPPORTS_ARROW", False)
                or getattr(_mod, "FS_SUPPORTS_EXCLUDE_SET", False)
            ):
                exclude_handle = _build(list(frozen_exclude))
            else:
                _warn_stale_native_wheel_once(len(frozen_exclude))
        except Exception:
            exclude_handle = None

    def _frames(block_df, n: int) -> list:
        """Frames to score for one block: the block itself when within
        bounds; auto-split sub-blocks when oversized (same semantics as the
        bucket lane's _split_oversized: skip on skip_oversized=True, score
        whole on split failure when skip_oversized=False)."""
        if n <= max_block_size:
            return [block_df]
        if skip_oversized:
            return []
        try:
            subs = _auto_split_block(
                block_df, max_block_size, "__external_oversized__"
            )
        except Exception:
            logger.error(
                "external-blocks auto-split failed for an oversized block "
                "(%d rows).", n, exc_info=True,
            )
            subs = []
        useful = []
        for b in subs:
            try:
                n_sub = b.n_rows()
            except Exception:
                n_sub = n + 1
            if 2 <= n_sub < n:
                useful.append(b.materialize().native)
        if useful:
            return useful
        logger.error(
            "Oversized external block (%d rows > max_block_size=%d, ~%s "
            "pairs) could not be auto-split; scoring whole because "
            "skip_oversized=False. See #1826.",
            n, max_block_size, f"{n * (n - 1) // 2:,}",
        )
        return [block_df]

    def _height(df) -> int:
        return df.height if hasattr(df, "height") else df.num_rows

    out: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    for block in blocks:
        bdf = block.materialize().native
        n = _height(bdf)
        if n < 2:
            continue
        for frame in _frames(bdf, n):
            if use_native:
                pairs = score_probabilistic_bucket_native(
                    frame, [_height(frame)], mk, em_result, frozen_exclude,
                    exclude_handle=exclude_handle,
                )
            else:
                pairs = prob_scorer(frame, frozen_exclude)
            for a, b, s in pairs:
                if target_ids is not None and (
                    (a in target_ids) == (b in target_ids)
                ):
                    continue
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                out.append((a, b, s))
    return out


def score_buckets(
    prepared_df: pl.DataFrame,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    n_buckets: int | None = None,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    em_result=None,
    _emit: str = "list",
) -> Any:
    """Score all blocks via hash-bucketed partition_by, no per-block LazyFrame.

    ``_emit`` (internal): ``"list"`` (default) returns
    ``list[tuple[int,int,float]]`` and mutates ``matched_pairs`` in place — the
    historical contract, byte-identical. ``"arrow"`` returns a
    ``PAIR_STREAM_SCHEMA`` ``pa.Table`` accumulated INCREMENTALLY (each pass's
    tuples are converted to Arrow int64/float64 columns and dropped, and the
    ``matched_pairs`` exclude set is NOT built) — the FS pair-stream memory win
    (PR-B, ``2026-07-18-fs-arrow-pair-stream-design.md``). Call it via
    ``score_buckets_arrow``; the arrow route is eligibility-gated to callers with
    no later exclude-set consumer (duplicate edges collapse in Union-Find, so the
    cross-pass exclude is a perf optimization, not correctness).

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
    # D2s-d1: dual-rep entry (Frame at D2s-d2); scalar reads via the seam.
    from goldenmatch.core.frame import to_frame as _tf_entry

    _prep_frame = _tf_entry(prepared_df)
    prepared_df = _prep_frame.native
    if _prep_frame.height == 0:
        return []
    # Resolve the block-key list HONORING multi-pass blocking: a `multi_pass`
    # config carries its keys in `.passes` with `.keys` empty (the schema
    # explicitly allows keys-OR-passes), so the empty-config guard must check
    # the RESOLVED pass list, not `.keys` alone. Guarding on `.keys` made an
    # explicit multi-pass union config (e.g. soundex(surname) + exact(email))
    # silently return zero pairs -> 0 clusters, while a single-key static config
    # worked -- issue #1048. `passes` is None for static/single-key configs, so
    # fall back to `keys`; only a config with NEITHER (nothing to block on)
    # returns empty, matching the no-candidate-pairs semantics.
    pass_keys = blocking_config.passes or blocking_config.keys
    if not pass_keys:
        return []

    # Probabilistic (Fellegi-Sunter) matchkeys ride the same bucket
    # orchestration as weighted, but score each block with the EM-trained
    # vectorized FS scorer instead of find_fuzzy_matches. The EM model must be
    # supplied (trained once by the caller via load_or_train_em).
    is_probabilistic = mk.type == "probabilistic"
    if is_probabilistic and em_result is None:
        raise ValueError(
            "score_buckets: probabilistic matchkey requires a trained em_result."
        )


    # Oversized-block skip (parity with polars-direct's build_blocks in
    # core/blocker.py). Read once and close over in the nested scoring
    # workers. A block is "oversized" when skip_oversized and size >
    # max_block_size; such blocks are skipped entirely (no pairs emitted),
    # matching polars' behavior when its _auto_split_block can't recover.
    skip_oversized = blocking_config.skip_oversized
    max_block_size = blocking_config.max_block_size

    if n_buckets is None:
        n_buckets = _default_n_buckets(_prep_frame.height)

    # Diag prints (flushed) so we can see substep timing on runner heartbeats
    # independent of the bench stage recorder, which only logs CLOSED stages.
    # Three 5M Linux runs hung mid-score_buckets with no substage closing;
    # these prints expose the actual hang line.
    _t0 = time.perf_counter()
    if _bkt_debug_on():
        print(f"[score_buckets] entry: prepared_df.height={_prep_frame.height} n_buckets={n_buckets}", flush=True)

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

    # Vectorized fast-path lane band (see _score_block_vec). Blocks with size in
    # [vec_min, vec_max] route through the batched-matrix scorer instead of the
    # Python per-pair double loop. vec_min: below it the per-pair loop wins (no
    # numpy alloc / triu overhead on tiny blocks -- the regime the fast path was
    # built for). vec_max: caps the float64 NxN so a pathological wide block
    # can't blow memory; above it the per-pair loop (or oversized-skip) handles
    # it. Both tunable for the cross-over sweep (scripts/bench_lowscale.py).
    #
    # Default vec_min=32 is the measured Pareto-safe floor (scripts/bench_lowscale.py,
    # 2026-06-12): per-block the lane is ~2.3x at n=50 and ~3.8x at n=1000, but it
    # REGRESSES on tiny blocks; the end-to-end cross-over sweep on the realistic_person
    # soundex shape breaks even at vec_min~16 and is net-positive by 32, so 32 never
    # makes a workload slower while still capturing the win on mid/large blocks.
    _vec_min = int(os.environ.get("GOLDENMATCH_BUCKET_VEC_MIN", "32"))
    _vec_max = int(os.environ.get("GOLDENMATCH_BUCKET_VEC_MAX", "2000"))

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
            if "__source__" in _prep_frame.columns:
                keep.append("__source__")
            keep.extend(c for c in _prep_frame.columns if c.startswith("__xform_"))
            # Probabilistic scoring (score_probabilistic_vectorized) reads the
            # RAW field columns and applies transforms itself, so keep them
            # (the weighted fast path uses __xform_* and doesn't need these).
            if is_probabilistic:
                for f in mk.fields:
                    if f.field in _prep_frame.columns and f.field not in keep:
                        keep.append(f.field)
                # FS negative-evidence fields: the probabilistic scorer path
                # reads these the same raw-column way (_ne_fired /
                # _field_values_for_block), and an NE-only field (the
                # canonical phone example -- not in mk.fields) would
                # otherwise be projected away here and never fire on the
                # default bucket backend. Also keep derive_from source
                # columns for completeness, though precompute_matchkey_
                # transforms already materializes the synthesized ne.field
                # column upstream of this projection.
                for ne in (mk.negative_evidence or []):
                    if ne.field in _prep_frame.columns and ne.field not in keep:
                        keep.append(ne.field)
                    for src in (ne.derive_from or []):
                        if src in _prep_frame.columns and src not in keep:
                            keep.append(src)
            # Source fields the block-key expression reads. Multi-key blocking
            # (rare today) accumulates fields across every key in the config.
            block_key_sources: set[str] = set()
            for key in pass_keys:
                block_key_sources.update(key.fields)
            for col in block_key_sources:
                if col in _prep_frame.columns and col not in keep:
                    keep.append(col)
            # The find_fuzzy_matches fallback (used when the fast path can't
            # resolve a scorer -- ensemble / embedding / etc.) reads the RAW
            # matchkey + negative-evidence field columns and applies the
            # transforms itself. Keep them, else those fields silently vanish and
            # bucket diverges from the legacy per-block path (e.g. NE penalty
            # dropped -> a pair legacy separates gets merged).
            for _f in (mk.fields or []):
                if _f.field in _prep_frame.columns and _f.field not in keep:
                    keep.append(_f.field)
            for _ne in (getattr(mk, "negative_evidence", None) or []):
                _nf = getattr(_ne, "field", None)
                if _nf and _nf in _prep_frame.columns and _nf not in keep:
                    keep.append(_nf)
            slim_frame = _prep_frame.select(keep)
            slim_df = slim_frame.native
            if _bkt_debug_on():
                print(
                    f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: slim projection "
                    f"{len(_prep_frame.columns)} -> {len(keep)} cols",
                    flush=True,
                )
    else:
        slim_frame = _prep_frame
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

    # Probabilistic matchkeys decline the weighted fast path (fast_path_specs is
    # None) and fall to _score_one_bucket, which dispatches to this FS scorer.
    # Resolved once (vectorized NxN by default; scalar fallback for model-backed
    # scorers) — mirrors the pipeline's probabilistic_block_scorer.
    prob_scorer = None
    fs_bucket_native = False
    if is_probabilistic:
        from goldenmatch.core.probabilistic import (
            _fs_native_eligible,
            probabilistic_block_scorer,
        )
        prob_scorer = probabilistic_block_scorer(mk, em_result)
        # Batched native FS: score a WHOLE block-sorted bucket in one
        # score_block_pairs_fs call (the FS analog of the weighted fast path's
        # single score_block_pairs_arrow call), instead of one
        # score_probabilistic_native call per block. Byte-identical to the
        # per-block loop by construction (the kernel isolates blocks by the
        # sizes list). GOLDENMATCH_FS_BUCKET_NATIVE=0 forces the per-block loop.
        fs_bucket_native = _fs_bucket_native_enabled() and _fs_native_eligible(mk)

    # #1803 item 1: build the FS exclude handle ONCE here, before the bucket
    # worker loop — the FS analog of the weighted path's Track 1 Fix B below.
    # Without it every bucket call re-marshals frozen_exclude as a Vec and the
    # kernel rebuilds a Rust HashSet per call (O(buckets x |exclude|), the
    # #552/#688 pathology). Old wheels (no FS_SUPPORTS_ARROW / _EXCLUDE_SET)
    # keep the legacy Vec path — _score_fs_native_frame checks the consts and
    # falls back byte-identically.
    fs_exclude_handle = None
    if fs_bucket_native and frozen_exclude:
        try:
            _mod = native_module()
            _fs_build = getattr(_mod, "build_exclude_set", None)
            if _fs_build is not None and (
                getattr(_mod, "FS_SUPPORTS_ARROW", False)
                or getattr(_mod, "FS_SUPPORTS_EXCLUDE_SET", False)
            ):
                _t_feb = time.perf_counter()
                fs_exclude_handle = _fs_build(list(frozen_exclude))
                if _bkt_debug_on():
                    print(
                        f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: "
                        f"fs build_exclude_set({len(frozen_exclude)} pairs) in "
                        f"{time.perf_counter()-_t_feb:.2f}s",
                        flush=True,
                    )
            else:
                _warn_stale_native_wheel_once(len(frozen_exclude))
        except Exception:
            fs_exclude_handle = None

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
            # Wheel-skew guard: the `date` scorer (id 4) exists only in kernels
            # that also expose `date_similarity`. A stale published wheel would
            # dispatch id 4 to score_one's catch-all and silently score every
            # date pair 0.0 -- so if any field is `date` and the loaded kernel
            # lacks the symbol, decline native entirely and let the pure-Python
            # per-pair path (which mirrors the kernel) score the whole block.
            _mod = native_module()
            _date_ok = _mod is not None and hasattr(_mod, "date_similarity")
            _date_diff_ok = _mod is not None and hasattr(_mod, "date_diff_similarity")
            _geo_ok = _mod is not None and hasattr(_mod, "geo_haversine_similarity")
            _qgram_ok = _mod is not None and hasattr(_mod, "qgram_similarity")
            _soundex_ok = _mod is not None and hasattr(_mod, "soundex_similarity")
            _dice_ok = _mod is not None and hasattr(_mod, "dice_similarity")
            _jaccard_ok = _mod is not None and hasattr(_mod, "jaccard_similarity")
            _phash_ok = _mod is not None and hasattr(_mod, "phash_similarity")
            _ensemble_ok = _mod is not None and hasattr(_mod, "ensemble_similarity")
            _radial_ok = _mod is not None and hasattr(_mod, "radial_similarity")
            _audio_fp_ok = _mod is not None and hasattr(_mod, "audio_fp_similarity")
            has_date = any(spec[3] == "date" for spec in _field_specs)
            has_date_diff = any(spec[3] == "date_diff" for spec in _field_specs)
            has_geo = any(spec[3] == "geo_haversine" for spec in _field_specs)
            has_qgram = any(spec[3] == "qgram" for spec in _field_specs)
            has_soundex = any(spec[3] == "soundex_match" for spec in _field_specs)
            has_dice = any(spec[3] == "dice" for spec in _field_specs)
            has_jaccard = any(spec[3] == "jaccard" for spec in _field_specs)
            has_phash = any(spec[3] == "phash" for spec in _field_specs)
            has_ensemble = any(spec[3] == "ensemble" for spec in _field_specs)
            has_radial = any(spec[3] == "radial" for spec in _field_specs)
            has_audio_fp = any(spec[3] == "audio_fp" for spec in _field_specs)
            has_initialism = any(spec[3] == "initialism_match" for spec in _field_specs)
            # initialism_match (id 7) has a TWO-part guard: the capability symbol
            # AND a successful legal-form install (id 7 scores against an empty
            # legal-form set until the host ships `entity_form_variants()`).
            # `_ensure_legal_forms_installed` folds both checks (symbol presence +
            # install) into one memoized bool, so an uninstalled/stale kernel
            # declines to the pure per-pair mirror instead of dropping no legal
            # forms. Only evaluated when a field actually uses initialism (avoids
            # the refdata load + install on every unrelated block).
            _initialism_ok = has_initialism and _ensure_legal_forms_installed()
            has_alias = any(spec[3] == "alias_match" for spec in _field_specs)
            # alias_match (id 8) has the same TWO-part guard as initialism: the
            # capability symbol AND a successful business+given-name table install
            # (id 8 scores against empty tables until the host ships them).
            # `_ensure_alias_tables_installed` folds both into one memoized bool;
            # only evaluated when a field actually uses alias_match.
            _alias_ok = has_alias and _ensure_alias_tables_installed()
            # name scorers (bucket ids 15/16) have the same TWO-part guard: the
            # `NATIVE_SUPPORTS_NAME_BUCKET_SCORERS` capability flag AND a successful
            # census/alias table install, folded into `_ensure_name_tables_installed`.
            # `_resolve_fast_path` already keeps a name field OFF the fast path when
            # this is False (or when name_freq_weighted_jw carries a tf table), so a
            # fast-path matchkey reaching here with a name field is table-installed;
            # the guard is defense-in-depth (a stale wheel scores ids 15/16 as 0.0).
            has_name_scorer = any(
                spec[3] in ("name_freq_weighted_jw", "given_name_aliased_jw")
                for spec in _field_specs
            )
            _name_ok = has_name_scorer and _ensure_name_tables_installed()
            # Wheel-skew: decline native entirely when a field uses a scorer whose
            # capability symbol the loaded kernel lacks (score_one would silently
            # zero that id); the pure-Python per-pair mirror scores the block.
            _skew_block = (
                (has_date and not _date_ok)
                or (has_date_diff and not _date_diff_ok)
                or (has_geo and not _geo_ok)
                or (has_qgram and not _qgram_ok)
                or (has_soundex and not _soundex_ok)
                or (has_initialism and not _initialism_ok)
                or (has_alias and not _alias_ok)
                or (has_name_scorer and not _name_ok)
                or (has_dice and not _dice_ok)
                or (has_jaccard and not _jaccard_ok)
                or (has_phash and not _phash_ok)
                or (has_ensemble and not _ensemble_ok)
                or (has_radial and not _radial_ok)
                or (has_audio_fp and not _audio_fp_ok)
            )
            if all(i is not None for i in ids) and not _skew_block:
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
            if _bkt_debug_on():
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
        # D5d: seam sort + run_lengths (== the old maintain_order agg on
        # key-sorted input; arrow twin is vectorized run_end_encode).
        from goldenmatch.core.frame import to_frame as _tf

        sorted_frame = _tf(bucket_df).sort(["__block_key__"])
        sorted_df = sorted_frame.native
        size_list = sorted_frame.run_lengths("__block_key__")
        if not size_list:
            return [], 0
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
                from goldenmatch.core.frame import is_polars_dataframe as _ipd

                if _ipd(sorted_df):
                    native_sorted_df = sorted_df.filter(pl.Series(row_mask))
                else:  # pa.Table lane: Table.filter takes a boolean array
                    import pyarrow as _pa

                    native_sorted_df = sorted_df.filter(_pa.array(row_mask))
                kept_size_list = [s for s, k in zip(size_list, keep) if k]
                if not kept_size_list:
                    return [], 0
            from goldenmatch.core.frame import is_polars_dataframe as _ipd2

            if _ipd2(native_sorted_df):
                row_ids_arrow = native_sorted_df["__row_id__"].cast(pl.Int64).to_arrow()
                field_arrays_arrow = [
                    native_sorted_df[col].to_arrow()
                    for col, _w, _fn, _name in field_specs
                ]
            else:  # pa.Table lane: already arrow -- combine chunks for the FFI
                import pyarrow as _pa
                import pyarrow.compute as _pc

                row_ids_arrow = _pc.cast(
                    native_sorted_df.column("__row_id__").combine_chunks(), _pa.int64()
                )
                field_arrays_arrow = [
                    native_sorted_df.column(col).combine_chunks()
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
        from goldenmatch.core.frame import to_frame as _to_frame_d5

        _sf = _to_frame_d5(sorted_df)
        row_ids = _sf.column("__row_id__").to_list()
        field_arrays = [
            _sf.column(col).to_list() for col, _w, _fn, _name in field_specs
        ]
        score_fns = [fn for _col, _w, fn, _name in field_specs]
        n_fields = len(field_specs)
        # NE per-pair specs (post-2026-05-29 widening). Pre-materialize the
        # NE xform columns; empty when NE missing / all-broken (no overhead).
        ne_arrays = [_sf.column(col).to_list() for col, _fn, _t, _p in ne_specs]
        ne_fns = [fn for _col, fn, _t, _p in ne_specs]
        ne_thresholds = [t for _col, _fn, t, _p in ne_specs]
        ne_penalties = [p for _col, _fn, _t, p in ne_specs]
        n_ne = len(ne_specs)
        # Vectorized-lane eligibility (decided once per bucket). Engages only
        # when: no negative evidence (penalty math stays per-pair), every field
        # scorer has a byte-identical matrix form (_VEC_SUPPORTED), and no nulls
        # in any field column -- the per-pair loop skips null fields, and
        # replicating that mask vectorized is extra surface for no low-scale
        # gain, so a column with nulls falls back to the per-pair loop wholesale.
        vec_scorer_names: list[str] | None = None
        if n_ne == 0 and all(name in _VEC_SUPPORTED for _c, _w, _fn, name in field_specs):
            if all(_sf.column(col).null_count() == 0 for col, _w, _fn, _name in field_specs):
                vec_scorer_names = [name for _c, _w, _fn, name in field_specs]
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
                # Vectorized lane: batched-matrix scoring for mid-sized blocks
                # (byte-parity with the per-pair branch below; see
                # _score_block_vec). Tiny blocks fall through to the per-pair
                # loop where numpy alloc/triu overhead would dominate.
                if vec_scorer_names is not None and _vec_min <= size <= _vec_max:
                    local_pairs.extend(
                        _score_block_vec(
                            row_ids, field_arrays, vec_scorer_names, weights,
                            offset, end, total_weight, threshold, frozen_exclude,
                        )
                    )
                    local_blocks += 1
                    offset += size
                    continue
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
                        # #weighted-null: renormalize by OBSERVED weight -- mirrors
                        # native/src/score.rs and core/scorer.py::score_pair.
                        combined = score_sum / weight_sum
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
        from goldenmatch.core.frame import to_frame as _tf

        sorted_frame = _tf(bucket_df).sort(["__block_key__"])
        sorted_df = sorted_frame.native
        # Pre-materialized run sizes (seam run_lengths == the old
        # maintain_order agg on key-sorted input; the inner loop stays
        # Polars-scalar-indexing-free, the hottest line at 1.67M blocks).
        size_list = sorted_frame.run_lengths("__block_key__")
        if not size_list:
            return [], 0

        def _split_oversized(block_df, size: int) -> list:
            """Auto-split an oversized block -- #1790 parity on the bucket
            lane (#1826: a 388K-row block through the vectorized scorer is a
            1.1 TiB dense-matrix allocation; through the native kernel it is
            ~75G pair comparisons). Returns the block frames to score:
            useful sub-blocks when the split works; [] when it fails and
            skip_oversized=True (polars-direct skips too); [block_df] when it
            fails and skip_oversized=False (the blocker.py opt-in "process
            anyway" semantics, ERROR-logged -- the vectorized scorer's dense
            guard still refuses truly impossible sizes)."""
            from goldenmatch.core.blocker import _auto_split_block

            if skip_oversized:
                # skip_oversized=True keeps the bucket lane's historical SKIP.
                # polars-direct's build_blocks hot-splits here too -- that
                # True-side divergence is the documented pre-existing gap
                # (consumers like autoconfig's probe passes are calibrated
                # against the bucket skip; splitting a degenerate constant-key
                # probe block detonated 18M pairs in autoconfig verify).
                # The #1826 fix below targets the DEFAULT skip_oversized=False
                # path, where the alternative was scoring the mega-block whole.
                return []
            try:
                subs = _auto_split_block(
                    block_df, max_block_size, "__bucket_oversized__"
                )
            except Exception:
                logger.error(
                    "bucket auto-split failed for an oversized block (%d rows).",
                    size, exc_info=True,
                )
                subs = []
            useful = []
            for b in subs:
                try:
                    n_sub = b.n_rows()
                except Exception:
                    n_sub = size + 1
                if 2 <= n_sub < size:
                    useful.append(b.materialize().native)
            if useful:
                return useful
            logger.error(
                "Oversized block (%d rows > max_block_size=%d, ~%s pairs) "
                "could not be auto-split; scoring whole because "
                "skip_oversized=False. See #1826.",
                size, max_block_size, f"{size * (size - 1) // 2:,}",
            )
            return [block_df]

        # Batched native FS: hand the WHOLE block-sorted bucket + its per-block
        # run-length sizes to the kernel in ONE call (the FS analog of
        # _score_one_bucket_fast's single score_block_pairs_arrow call). The
        # kernel isolates blocks by the sizes list, so this is byte-identical to
        # calling prob_scorer (score_probabilistic_native) per block. Mirror the
        # fast path's oversized/size<2 keep mask + array-and-size filtering, then
        # apply the same across_files_only / target_ids post-filters as the
        # per-block loop. GOLDENMATCH_FS_BUCKET_NATIVE=0 -> fs_bucket_native False
        # -> the per-block prob_scorer loop below (parity escape hatch).
        if fs_bucket_native:
            # Oversized blocks are ALWAYS excluded from the batched call and
            # handled by _split_oversized below (score sub-blocks; skip or
            # score-whole per skip_oversized) -- #1790 parity on this lane.
            keep = [2 <= s <= max_block_size for s in size_list]
            fs_sorted_df = sorted_df
            kept_size_list = size_list
            if not all(keep):
                import numpy as np

                row_mask = np.repeat(np.array(keep, dtype=bool), size_list)
                from goldenmatch.core.frame import is_polars_dataframe as _ipd

                if _ipd(sorted_df):
                    fs_sorted_df = sorted_df.filter(pl.Series(row_mask))
                else:  # pa.Table lane: Table.filter takes a boolean array
                    import pyarrow as _pa

                    fs_sorted_df = sorted_df.filter(_pa.array(row_mask))
                kept_size_list = [s for s, k in zip(size_list, keep) if k]
            from goldenmatch.core.probabilistic import (
                score_probabilistic_bucket_native,
            )

            pairs: list[tuple[int, int, float]] = []
            local_blocks = 0
            if kept_size_list:
                pairs = score_probabilistic_bucket_native(
                    fs_sorted_df, kept_size_list, mk, em_result, frozen_exclude,
                    exclude_handle=fs_exclude_handle,
                )
                local_blocks = sum(1 for s in kept_size_list if s >= 2)
            # Oversized blocks: auto-split, then score each resolved frame in
            # its own single-block native call (same kernel, size_list=[n], so
            # the emitted cells match a per-block run exactly).
            offset = 0
            for s in size_list:
                if s > max_block_size:
                    for sub in _split_oversized(sorted_df.slice(offset, s), s):
                        pairs.extend(
                            score_probabilistic_bucket_native(
                                sub, [len(sub)], mk, em_result, frozen_exclude,
                                exclude_handle=fs_exclude_handle,
                            )
                        )
                        local_blocks += 1
                offset += s
            if not pairs and local_blocks == 0:
                return [], 0
            # Same post-filters as the per-block loop below (the native kernel
            # doesn't know about source_lookup / target_ids).
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
            return pairs, local_blocks

        def _score_block_frame(block_df) -> list[tuple[int, int, float]] | None:
            """Score ONE block frame via the per-block scorer + the
            across_files / target post-filters (shared by normal blocks and
            auto-split sub-blocks). None = block pre-filtered out."""
            if across_files_only and source_lookup:
                sources_in_block = block_df["__source__"].unique().to_list()
                if len(sources_in_block) < 2:
                    return None
            if prob_scorer is not None:
                pairs = prob_scorer(block_df, frozen_exclude)
            else:
                # find_fuzzy_matches (the fallback the slow path uses for
                # scorers the fast path can't resolve -- ensemble / embedding /
                # etc.) accepts BOTH reps natively: it coerces via
                # ``core.frame.to_frame`` and its NE branch reads rows through a
                # ``to_pylist`` dual-rep, so a ``pa.Table`` needs no conversion.
                # Hand it the block as-is -- pre-converting a ``pa.Table`` to
                # polars here (the old ``pl.from_arrow`` bridge, plus the
                # ``isinstance(block_df, pl.DataFrame)`` probe) forced the polars
                # import and broke the arrow lane's polars-free guarantee (e.g.
                # goldengraph's zero-config resolve installs goldenmatch WITHOUT
                # polars). Parity with the legacy per-block path is unchanged:
                # a real polars block still flows through untouched.
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
            return pairs

        local_pairs: list[tuple[int, int, float]] = []
        local_blocks = 0
        offset = 0
        for size in size_list:
            if size >= 2:
                if size > max_block_size:
                    # Oversized: auto-split (#1790 parity, #1826) and score
                    # the resolved frames; _split_oversized already applied
                    # the skip_oversized / score-whole fallback semantics.
                    for sub in _split_oversized(
                        sorted_df.slice(offset, size), size
                    ):
                        pairs = _score_block_frame(sub)
                        if pairs is None:
                            continue
                        local_pairs.extend(pairs)
                        local_blocks += 1
                    offset += size
                    continue
                pairs = _score_block_frame(sorted_df.slice(offset, size))
                if pairs is None:
                    offset += size
                    continue
                local_pairs.extend(pairs)
                local_blocks += 1
            offset += size
        return local_pairs, local_blocks

    # Bounded bucket streaming (FS route only): when on, the scale branch below
    # does NOT partition the keyed frame into all n_buckets eager frames. It
    # keeps the single `bucketed` frame resident and slices each bucket out on
    # demand (filter_eq inside the worker), so peak holds `bucketed` (~1x) plus
    # at most max_workers in-flight slices instead of the ~2x transient double at
    # partition time (the "partition" stage jump). Gated to the probabilistic
    # (FS) path -- fast_path_specs is None there -- and off by default; the
    # eager path is byte-identical (same rows, same within-bucket order via
    # filter_eq == partition_by(maintain_order), and cross-bucket order is
    # unordered downstream: thread-pool scored + pairs canonicalized).
    _fs_streaming = is_probabilistic and _fs_bounded_stream_enabled()

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
        with stage("bucket_assign"):
            _ta = time.perf_counter()
            # D5d: seam block-key derivation (derive_block_key is the W2a
            # twin of _build_block_key_expr, both backends).
            from goldenmatch.core.frame import to_frame as _tf

            _slim_frame = _tf(slim_df)
            keyed = (
                _slim_frame.with_column(
                    "__block_key__",
                    _slim_frame.derive_block_key(
                        key.fields, key.transforms or [],
                        field_transforms=getattr(key, "field_transforms", None),
                    ),
                )
                # A null block key means "this row CANNOT be blocked" -- NOT "this
                # row blocks with every other unblockable row". Without this, every
                # null-key row hashes into ONE bucket and is scored against every
                # other: on the ER person shape at 1M that is 9,846 rows -> 48.5M
                # comparisons, while the largest LEGITIMATE postcode block is 25.
                #
                # Measured cost of NOT filtering (1M person):
                #   wall 31.81s -> 21.44s; FP 8,682 -> 111 (precision .9626 -> .9995)
                #   recall .9319 -> .9315 (the null block gave +81 TP / +8,571 FP)
                # One kernel call took 20.482s of 22.669s: 256 buckets across 12
                # workers all serialized behind that single straggler.
                #
                # This is build_blocks' own guard -- it filters the derived key the
                # same way: drop null + the nan/null/none stringified-missing
                # sentinels, keep "" (#390).
                #
                # KNOWN GAP (pre-existing, not introduced here): an empty-string key
                # arrives here ALREADY NULL (something upstream in prepare nulls
                # ""), so filter_valid_key's keep-"" branch is unreachable on this
                # path and ""-keyed rows drop with the true nulls. Before this filter
                # they matched via the null mega-block -- #390's intent met by
                # accident, at the cost of the FP/perf blowup above. Preserving
                # ""-vs-null through prepare is a separate fix; pinned as an
                # xfail(strict) in tests/test_bucket_null_block_key.py.
                .filter_valid_key("__block_key__")
                .native
            )
            if _bkt_debug_on():
                print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: keyed (with_columns key_expr) in {time.perf_counter()-_ta:.2f}s", flush=True)

        # #422 fix 1: small-block fast path. When prepared_df.height < n_buckets,
        # the hash + partition_by step always collapses to 1 non-empty bucket
        # (every row hashes into the same bucket-output because most buckets
        # are empty by pigeonhole). The bookkeeping is pure overhead. Skip
        # straight to treating `keyed` as the single bucket and scoring.
        # On the streaming-block sync caller, this hits on every per-block
        # invocation (block size ~8, n_buckets default 32-128).
        stream_bucket_ids: list | None = None  # set only on the streaming scale branch
        if _prep_frame.height < n_buckets:
            bucketed = keyed
            # Wrap in a single-bucket dict to share the scoring path below
            # (native frames: pl.DataFrame or pa.Table by lane). The single
            # bucket is already bounded, so streaming never engages here.
            buckets_dict: dict[Any, Any] = {0: bucketed}
            if _bkt_debug_on():
                print(
                    f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: "
                    f"small-block fast path (height={_prep_frame.height} < n_buckets={n_buckets}); "
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
                # D2s-c: per-lane bucket assignment via the seam (polars impl
                # is this stage's old hash(seed) % n expr verbatim; buckets
                # are shard-internal, never output-visible).
                bucketed = (
                    _tf(keyed)
                    .with_bucket_column(
                        "__block_key__", "__bucket__", n_buckets, BUCKET_HASH_SEED
                    )
                    .native
                )
            if _bkt_debug_on():
                print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: bucketed (hash %% N) in {time.perf_counter()-_tb:.2f}s", flush=True)

            with stage("bucket_partition"):
                _tp = time.perf_counter()
                from goldenmatch.core.frame import to_frame as _tf

                if _fs_streaming:
                    # Bounded streaming: DON'T build N eager frames. Keep
                    # `bucketed` resident and record the non-empty bucket ids
                    # (a bucket id is present iff >=1 row hashed into it, so the
                    # distinct set IS the non-empty set). Take the distinct ids
                    # off the single `__bucket__` column via Column.unique()
                    # (vectorized -- pc.unique on the arrow lane, .unique() on
                    # polars), NOT frame-level unique_by (which materializes a
                    # full deduped frame across ALL columns and, on the arrow
                    # lane, loops through .to_pylist() -- an O(N) transient that
                    # would defeat the whole point of streaming). The distinct
                    # set is <= n_buckets ints. Sorted for a deterministic
                    # (order-invariant) scoring order.
                    _stream_frame = _tf(bucketed)
                    stream_bucket_ids = sorted(
                        _stream_frame.column("__bucket__").unique().to_list()
                    )
                    buckets_dict = None  # sentinel: streaming, no eager frames
                    del keyed  # `bucketed` (via _stream_frame) retained for slicing
                    if _bkt_debug_on():
                        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: stream bucket-id scan in {time.perf_counter()-_tp:.2f}s -> {len(stream_bucket_ids)} buckets (no eager partition)", flush=True)
                else:
                    # First-level partition via the seam (group_partitions ==
                    # partition_by with unwrapped keys; N eager frames).
                    buckets_dict = {
                        k: part.native
                        for k, part in _tf(bucketed).group_partitions("__bucket__")
                    }
                    # Free the pre-partition `keyed` and `bucketed` parents.
                    # group_partitions built N independent eager frames; the
                    # original contiguous parents are dead weight now.
                    del keyed
                    del bucketed
                    stream_bucket_ids = None
                    if _bkt_debug_on():
                        print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: partition_by(bucket) in {time.perf_counter()-_tp:.2f}s -> {len(buckets_dict)} buckets", flush=True)

        with stage("bucket_post_partition_setup"):
            if _fs_streaming and stream_bucket_ids is not None:
                # No materialized frames; every present bucket id is non-empty.
                non_empty_buckets = None
                n_non_empty_buckets = len(stream_bucket_ids)
            else:
                # len() works on both native frames (pl rows / pa num_rows).
                non_empty_buckets = [b for b in buckets_dict.values() if len(b) > 0]
                n_non_empty_buckets = len(non_empty_buckets)
        if _bkt_debug_on():
            print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: {n_non_empty_buckets} non-empty buckets ready for scoring", flush=True)

        # B2c: in arrow mode convert EACH bucket's tuples to Arrow the moment it
        # returns and drop the Python list, so peak holds one bucket's tuples +
        # the accumulated int64/float64 columns -- never a per-PASS list[tuple]
        # (the remaining transient after B2a). List mode is byte-unchanged.
        _arrow_mode = _emit == "arrow"
        pass_pairs: list[tuple[int, int, float]] = []
        bucket_tables: list[Any] = []
        pass_emitted = 0
        pass_blocks_scored = 0
        if n_non_empty_buckets == 0:
            empty: Any = pairs_to_pair_stream([]) if _arrow_mode else pass_pairs
            return empty, pass_blocks_scored, n_non_empty_buckets

        def _sink(bucket_pairs: list) -> None:
            nonlocal pass_emitted
            pass_emitted += len(bucket_pairs)
            if _arrow_mode:
                bucket_tables.append(pairs_to_pair_stream(bucket_pairs))
            else:
                pass_pairs.extend(bucket_pairs)

        with stage("bucket_score"):
            # rapidfuzz.cdist releases the GIL inside the scorer, so threads
            # give real parallelism. Mirror score_blocks_parallel's worker cap.
            max_workers = min(n_non_empty_buckets, os.cpu_count() or 4)
            _ts = time.perf_counter()
            worker = _score_one_bucket_fast if fast_path_specs is not None else _score_one_bucket
            if fast_path_specs is not None:
                path_label = "fast"
            elif is_probabilistic:
                path_label = "probabilistic_vectorized"
            else:
                path_label = "find_fuzzy_matches"
            _streaming_now = _fs_streaming and stream_bucket_ids is not None
            if _streaming_now:
                # Slice one bucket off the resident `bucketed` frame on demand,
                # score it, then free the slice -- so at most max_workers slices
                # are live at once (bounded). filter_eq preserves within-bucket
                # order == the eager partition, so `worker`'s output is
                # byte-identical to the eager path per bucket.
                from goldenmatch.core.frame import to_frame as _tf

                _bucketed_frame = _tf(bucketed)

                def _stream_worker(bid: Any) -> tuple[list, int]:
                    slice_native = _bucketed_frame.filter_eq("__bucket__", bid).native
                    try:
                        return worker(slice_native)
                    finally:
                        del slice_native

                items: Any = stream_bucket_ids
                run_worker: Any = _stream_worker
                path_label += "+stream"
            else:
                items = non_empty_buckets
                run_worker = worker
            if _bkt_debug_on():
                print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: starting bucket_score with max_workers={max_workers} path={path_label}", flush=True)
            if max_workers <= 1 or n_non_empty_buckets <= 2:
                for item in items:
                    pairs, n = run_worker(item)
                    _sink(pairs)
                    del pairs
                    pass_blocks_scored += n
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    for pairs, n in pool.map(run_worker, items):
                        _sink(pairs)
                        del pairs
                        pass_blocks_scored += n
        if _bkt_debug_on():
            print(f"[score_buckets] t={time.perf_counter()-_t0:.2f}s: bucket_score done in {time.perf_counter()-_ts:.2f}s, {pass_blocks_scored} blocks, {pass_emitted} pairs", flush=True)
        if _arrow_mode:
            import pyarrow as _pa

            result: Any = (
                _pa.concat_tables(bucket_tables) if bucket_tables
                else pairs_to_pair_stream([])
            )
            return result, pass_blocks_scored, n_non_empty_buckets
        return pass_pairs, pass_blocks_scored, n_non_empty_buckets

    _arrow = _emit == "arrow"
    all_pairs: list[tuple[int, int, float]] = []
    pass_tables: list[Any] = []  # arrow mode: one pa.Table per pass
    n_emitted = 0
    total_blocks_scored = 0
    total_non_empty = 0
    slim_cols = set(slim_frame.columns)
    for key in pass_keys:
        if not set(key.fields) <= slim_cols:
            logger.warning(
                "score_buckets: skipping pass %s -- field(s) %s absent from prepared_df",
                key.fields, sorted(set(key.fields) - slim_cols),
            )
            continue
        pass_result, blocks_scored, n_non_empty = _score_single_pass(key)
        if _arrow:
            # _score_single_pass already returns a PAIR_STREAM_SCHEMA pa.Table
            # (converted per BUCKET, B2c) -- accumulate it directly; no per-pass
            # list ever exists. See the PR-B design doc.
            n_emitted += pass_result.num_rows
            pass_tables.append(pass_result)
            del pass_result
        else:
            n_emitted += len(pass_result)
            all_pairs.extend(pass_result)
        total_blocks_scored += blocks_scored
        total_non_empty += n_non_empty
    if not _arrow:
        # Cross-pass exclude set for LATER scoring passes/matchkeys. Arrow mode
        # SKIPS it (its ~8 GB at 66M pairs is half the OOM): duplicate edges
        # collapse in Union-Find, so the exclude is a perf optimization, not
        # correctness, and the arrow route is gated to callers with no later
        # exclude consumer.
        for a, b, _s in all_pairs:
            matched_pairs.add((min(a, b), max(a, b)))

    record_metrics({
        "bucket_count": total_non_empty,
        "bucket_n_target": n_buckets,
        "block_count_scored": total_blocks_scored,
    })
    logger.info(
        "score_buckets: %d non-empty buckets (target N=%d), %d blocks scored, %d pairs",
        total_non_empty, n_buckets, total_blocks_scored, n_emitted,
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

        if _bkt_debug_on():
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
    if _arrow:
        import pyarrow as _pa

        if pass_tables:
            return _pa.concat_tables(pass_tables)
        return pairs_to_pair_stream([])
    return all_pairs


def pairs_to_pair_stream(pairs: list[tuple[int, int, float]]) -> Any:
    """``list[(id_a, id_b, score)]`` -> a ``PAIR_STREAM_SCHEMA_SPEC`` ``pa.Table``
    (``id_a``/``id_b`` int64, ``score`` float64) — the Arrow pair-stream shape
    ``cluster.build_clusters_arrow_native`` consumes.

    B1 seam for the FS Arrow-native pair-stream cutover
    (``docs/superpowers/specs/2026-07-18-fs-arrow-pair-stream-design.md``). Pair
    ``(a, b)`` order and any cross-pass DUPLICATE pairs are preserved verbatim —
    Union-Find collapses duplicate edges, so this is edge-faithful to the
    ``list[tuple]`` form. Empty input yields a zero-row table with the schema.
    """
    import pyarrow as _pa

    if not pairs:
        return _pa.table({
            "id_a": _pa.array([], _pa.int64()),
            "id_b": _pa.array([], _pa.int64()),
            "score": _pa.array([], _pa.float64()),
        })
    # Single left-to-right unzip (one temp list per column, dropped on return).
    id_a = [p[0] for p in pairs]
    id_b = [p[1] for p in pairs]
    score = [p[2] for p in pairs]
    return _pa.table({
        "id_a": _pa.array(id_a, _pa.int64()),
        "id_b": _pa.array(id_b, _pa.int64()),
        "score": _pa.array(score, _pa.float64()),
    })


def score_buckets_arrow(*args: Any, **kwargs: Any) -> Any:
    """Arrow pair-stream (``PAIR_STREAM_SCHEMA_SPEC`` ``pa.Table``) form of
    :func:`score_buckets`.

    B1 of the FS Arrow-native pair-stream cutover (design doc
    ``2026-07-18-fs-arrow-pair-stream-design.md``). Same scoring, same
    ``matched_pairs`` mutation, same cross-pass-duplicate semantics as
    ``score_buckets`` — it delegates, so it is byte-faithful by construction —
    but returns the emitted pairs as a ``pa.Table`` (``id_a``/``id_b`` int64,
    ``score`` float64) so the FS clustering path can consume Arrow via
    ``build_clusters_arrow_native`` instead of accumulating a
    ``list[tuple]`` + a ``matched_pairs`` set (~16 GB at 66M pairs vs ~1.3 GB
    Arrow — the 1M person OOM's second cause).

    Accumulates INCREMENTALLY (``_emit="arrow"``): each pass is converted to
    Arrow and its Python list dropped, and the ``matched_pairs`` exclude set is
    NOT built — so peak holds one pass's tuples + the accumulated int64/float64
    columns rather than the whole run's ``list[tuple]`` + an 8 GB set. Because it
    omits the ``matched_pairs`` mutation, the FS dedupe caller (B2b) routes here
    only when no later pass/matchkey consumes the exclude set. B2b threads the
    table into ``build_clusters_arrow_native``.
    """
    kwargs["_emit"] = "arrow"
    return score_buckets(*args, **kwargs)
