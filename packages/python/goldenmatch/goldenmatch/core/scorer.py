"""Scorer for GoldenMatch — field-level and pair-level scoring."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import jellyfish
import numpy as np
import polars as pl
from rapidfuzz.distance import JaroWinkler, Levenshtein
from rapidfuzz.fuzz import token_sort_ratio
from rapidfuzz.process import cdist

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core._profile_helpers import (
    hartigan_dip,
    histogram_20,
    mass_above,
    mass_borderline,
)
from goldenmatch.core.complexity_profile import ScoringProfile
from goldenmatch.core.profile_emitter import current_emitter
from goldenmatch.utils.transforms import apply_transforms

logger = logging.getLogger(__name__)


def _emit_scoring_profile(
    pairs: list[tuple[int, int, float]],
    threshold: float,
    *,
    candidates_compared: int = 0,
    per_field_variance: dict[str, float] | None = None,
) -> None:
    """Emit ScoringProfile to current emitter. No-op when emitter is null.

    Args:
        pairs: Pairs *above* the threshold (returned by find_fuzzy_matches).
        threshold: Score threshold used to filter pairs.
        candidates_compared: Total candidate pairs evaluated before threshold
            filtering.  Distinct from ``len(pairs)`` which only counts matches.
            Approximation: sum of n*(n-1)//2 for each block processed.
        per_field_variance: Optional per-field score variance dict.
    """
    scores = [s for _, _, s in pairs]
    profile = ScoringProfile(
        n_pairs_scored=len(scores),
        candidates_compared=candidates_compared,
        score_histogram=histogram_20(scores),
        dip_statistic=hartigan_dip(scores),
        mass_above_threshold=mass_above(scores, threshold),
        mass_in_borderline=mass_borderline(scores, threshold),
        per_field_score_variance=per_field_variance or {},
    )
    current_emitter().set_scoring(profile)


def score_field(val_a: str | None, val_b: str | None, scorer: str) -> float | None:
    """Score two field values using the specified scorer.

    Returns None if either value is None.
    """
    if val_a is None or val_b is None:
        return None

    if scorer == "exact":
        return 1.0 if val_a == val_b else 0.0
    elif scorer == "jaro_winkler":
        return JaroWinkler.similarity(val_a, val_b)
    elif scorer == "levenshtein":
        return Levenshtein.normalized_similarity(val_a, val_b)
    elif scorer == "token_sort":
        return token_sort_ratio(val_a, val_b) / 100.0
    elif scorer == "soundex_match":
        return 1.0 if jellyfish.soundex(val_a) == jellyfish.soundex(val_b) else 0.0
    elif scorer == "dice":
        return _dice_score_single(val_a, val_b)
    elif scorer == "jaccard":
        return _jaccard_score_single(val_a, val_b)
    else:
        # Check plugin registry
        from goldenmatch.plugins.registry import PluginRegistry
        plugin = PluginRegistry.instance().get_scorer(scorer)
        if plugin is not None:
            return plugin.score_pair(val_a, val_b)  # pyright: ignore[reportAttributeAccessIssue]  # plugin protocol is runtime-duck-typed
        raise ValueError(f"Unknown scorer: {scorer!r}")


def score_pair(row_a: dict, row_b: dict, fields: list[MatchkeyField]) -> float:
    """Score a pair of rows across all fields using weighted aggregation.

    Fields that produce None scores are excluded from the average.
    If all fields are None, returns 0.0.
    """
    weighted_sum = 0.0
    weight_sum = 0.0

    for f in fields:
        # score_pair expects fully-populated MatchkeyFields; upstream config
        # validation guarantees field/scorer/weight are non-None at call time.
        val_a = apply_transforms(row_a.get(f.field), f.transforms)  # pyright: ignore[reportArgumentType]
        val_b = apply_transforms(row_b.get(f.field), f.transforms)  # pyright: ignore[reportArgumentType]
        field_score = score_field(val_a, val_b, f.scorer)  # pyright: ignore[reportArgumentType]

        if field_score is not None:
            weighted_sum += field_score * f.weight  # pyright: ignore[reportOperatorIssue]
            weight_sum += f.weight  # pyright: ignore[reportOperatorIssue]

    if weight_sum == 0.0:
        return 0.0

    return weighted_sum / weight_sum


def _apply_negative_evidence(matchkey: MatchkeyConfig, pair: dict) -> float:
    """v1.11: compute the total negative-evidence penalty for a pair.

    Returns the sum of penalties for NE fields whose similarity is below
    their threshold. Defensive: skips NE entries with unknown scorers,
    missing fields, or scorer-call exceptions; logs WARNING and continues.

    ``pair`` maps field names to 2-tuples ``(val_a, val_b)`` — the same
    shape used by the scoring loop when passing raw row values. Fields not
    present in ``pair`` are silently skipped.

    Caller is responsible for: ``final_score = max(0.0, score_positive - penalty)``.

    Only applies to weighted matchkeys. Returns 0.0 immediately when
    ``matchkey.negative_evidence`` is None or empty.
    """
    if not matchkey.negative_evidence:
        return 0.0

    total_penalty = 0.0
    for ne in matchkey.negative_evidence:
        if ne.field not in pair:
            continue
        try:
            val_a, val_b = pair[ne.field]
            val_a = apply_transforms(val_a, ne.transforms)
            val_b = apply_transforms(val_b, ne.transforms)
            sim = score_field(val_a, val_b, ne.scorer)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "auto-config: NE scorer '%s' for field '%s' not registered or failed: %s; skipping",
                ne.scorer, ne.field, exc,
            )
            continue
        except Exception as exc:
            logger.warning(
                "auto-config: NE scoring of field '%s' raised %s; skipping",
                ne.field, type(exc).__name__,
            )
            continue
        if sim is None:
            # One or both values are None — can't score, skip
            continue
        if sim < ne.threshold:
            total_penalty += ne.penalty
    return total_penalty


def _apply_negative_evidence_to_exact_pairs(  # pyright: ignore[reportUnusedFunction]  # called from core/pipeline.py (outside slice)
    pairs: list[tuple[int, int, float]],
    matchkey: MatchkeyConfig,
    full_df: pl.DataFrame,
) -> list[tuple[int, int, float]]:
    """v1.12 Path Y: filter pairs from find_exact_matches by NE penalty.

    ``pairs`` is the output of find_exact_matches: list of (row_id_a, row_id_b, 1.0)
    where each pair already shares the matchkey value. v1.12: subtract NE
    penalties; emit only if final_score >= threshold.

    When matchkey.negative_evidence is None or empty: returns pairs unchanged
    (today's binary behavior preserved).
    """
    if not matchkey.negative_evidence:
        return pairs
    threshold = matchkey.threshold if matchkey.threshold is not None else 0.5
    if matchkey.threshold is None:
        logger.info(
            "auto-config: NE active on exact matchkey '%s' but threshold is None; "
            "using default 0.5 (recommend setting matchkey.threshold explicitly)",
            matchkey.name,
        )

    # Build a lookup of (row_id → row_index_in_full_df) for fast NE column access
    row_id_to_idx: dict[int, int] = dict(
        zip(full_df["__row_id__"].to_list(), range(full_df.height))
    )

    filtered: list[tuple[int, int, float]] = []
    for row_a, row_b, _initial_score in pairs:
        idx_a = row_id_to_idx.get(row_a)
        idx_b = row_id_to_idx.get(row_b)
        if idx_a is None or idx_b is None:
            # Defensive: shouldn't happen if pairs came from find_exact_matches
            continue
        pair_dict: dict = {}
        for ne in matchkey.negative_evidence:
            if ne.field not in full_df.columns:
                continue
            try:
                val_a = full_df[ne.field][idx_a]
                val_b = full_df[ne.field][idx_b]
                pair_dict[ne.field] = (val_a, val_b)
            except Exception:
                pair_dict[ne.field] = (None, None)
        penalty = _apply_negative_evidence(matchkey, pair_dict)
        final_score = max(0.0, 1.0 - penalty)
        if final_score >= threshold:
            filtered.append((row_a, row_b, final_score))
    return filtered


def find_exact_matches(
    lf: pl.LazyFrame, mk: MatchkeyConfig
) -> list[tuple[int, int, float]]:
    """Find exact matches by grouping on the matchkey column.

    Uses a Polars self-join on the matchkey column to find all pairs of
    __row_id__ that share the same matchkey value, each with score 1.0.
    Null matchkey values are excluded.
    """
    mk_col = f"__mk_{mk.name}__"
    df = lf.select("__row_id__", mk_col).collect()

    # Drop nulls — they should not match
    df = df.filter(pl.col(mk_col).is_not_null())

    if df.height < 2:
        return []

    # Self-join on matchkey — produces all (left, right) combinations per group
    joined = df.join(df, on=mk_col, suffix="_right")

    # Keep only pairs where left < right (avoid duplicates and self-matches)
    joined = joined.filter(pl.col("__row_id__") < pl.col("__row_id___right"))

    if joined.height == 0:
        return []

    ids_a = joined["__row_id__"].to_list()
    ids_b = joined["__row_id___right"].to_list()
    return [(a, b, 1.0) for a, b in zip(ids_a, ids_b)]


# ---------------------------------------------------------------------------
# Vectorized helpers for find_fuzzy_matches
# ---------------------------------------------------------------------------

def _get_transformed_values(block_df: pl.DataFrame, field: MatchkeyField) -> list:
    """Get transformed values for a field as a list.

    Fast path: read the precomputed __xform_<sig>__ column populated by
    precompute_matchkey_transforms (called once per pipeline run, eagerly,
    before blocking). Avoids one `.select()` per (block × matchkey field).

    Fallback path: legacy per-block .select(_try_native_chain(...)) for
    callers that bypass the pipeline (DataFrame entry points, tests calling
    find_fuzzy_matches directly).
    """
    from goldenmatch.core.matchkey import _try_native_chain, _xform_sig

    sig = _xform_sig(field)
    if sig in block_df.columns:
        return block_df[sig].to_list()

    col = field.field
    assert col is not None, "field.field must be set; upstream validation enforces"
    native_expr = _try_native_chain(col, field.transforms)
    if native_expr is not None:
        result_df = block_df.select(native_expr.alias("__tmp__"))
        return result_df["__tmp__"].to_list()

    values = block_df[col].to_list()
    return [apply_transforms(v, field.transforms) if v is not None else None for v in values]


def _exact_score_matrix(values: list) -> np.ndarray:
    """NxN exact match matrix using hash-based grouping."""
    n = len(values)
    scores = np.zeros((n, n))
    # Group indices by value (O(n) hash map)
    groups: dict[str, list[int]] = {}
    for i, v in enumerate(values):
        if v is not None:
            groups.setdefault(v, []).append(i)
    # For each group, set all pairs to 1.0
    for indices in groups.values():
        if len(indices) > 1:
            idx = np.array(indices)
            scores[np.ix_(idx, idx)] = 1.0
    return scores


def _fuzzy_score_matrix(
    values: list, scorer_name: str, model_name: str = "all-MiniLM-L6-v2",
) -> np.ndarray:
    """NxN fuzzy score matrix using rapidfuzz cdist or embedding cosine similarity."""
    if scorer_name == "embedding":
        try:
            from goldenmatch.core.embedder import get_embedder

            embedder = get_embedder(model_name)
            embeddings = embedder.embed_column(values, cache_key=f"_block_{id(values)}")
            sim = embedder.cosine_similarity_matrix(embeddings)
            return np.asarray(sim, dtype=np.float64)
        except Exception:
            logger.warning("Embedding scorer failed, falling back to token_sort", exc_info=True)
            scorer_name = "token_sort"

    # Replace None with empty string for cdist (we handle nulls separately)
    clean = [v if v is not None else "" for v in values]

    if scorer_name == "ensemble":
        # Combine multiple scorers, take element-wise max
        jw = np.asarray(cdist(clean, clean, scorer=JaroWinkler.similarity), dtype=np.float64)
        ts = np.asarray(cdist(clean, clean, scorer=token_sort_ratio), dtype=np.float64) / 100.0
        sx = _soundex_score_matrix(values) * 0.8
        matrix = np.maximum(np.maximum(jw, ts), sx)
    elif scorer_name == "jaro_winkler":
        matrix = cdist(clean, clean, scorer=JaroWinkler.similarity)
    elif scorer_name == "levenshtein":
        matrix = cdist(clean, clean, scorer=Levenshtein.normalized_similarity)
    elif scorer_name == "token_sort":
        matrix = cdist(clean, clean, scorer=token_sort_ratio) / 100.0
    elif scorer_name == "dice":
        return _dice_score_matrix(values)
    elif scorer_name == "jaccard":
        return _jaccard_score_matrix(values)
    else:
        raise ValueError(f"Unknown fuzzy scorer: {scorer_name!r}")

    return np.asarray(matrix, dtype=np.float64)


def _record_embedding_score_matrix(
    block_df: pl.DataFrame, columns: list[str], model_name: str = "all-MiniLM-L6-v2",
    column_weights: dict[str, float] | None = None,
) -> np.ndarray:
    """NxN score matrix from record-level embeddings.

    Concatenates columns into a single text string per record,
    embeds the full string, and computes cosine similarity.
    """
    from goldenmatch.core.embedder import get_embedder

    concat_values = []
    for row in block_df.iter_rows(named=True):
        parts = []
        for col in columns:
            if column_weights is not None:
                w = column_weights.get(col, 1.0)
                if w <= 0:
                    continue
                val = row.get(col)
                if val is not None:
                    part = f"{col}: {val}"
                    repeats = round(w) if w > 1.0 else 1
                    for _ in range(repeats):
                        parts.append(part)
            else:
                val = row.get(col)
                if val is not None:
                    parts.append(f"{col}: {val}")
        concat_values.append(" | ".join(parts) if parts else "")

    row_ids = block_df["__row_id__"].to_list()
    cache_key = f"_rec_emb_{hash(tuple(columns))}_{hash(tuple(row_ids))}"

    embedder = get_embedder(model_name)
    embeddings = embedder.embed_column(concat_values, cache_key=cache_key)
    sim = embedder.cosine_similarity_matrix(embeddings)
    return np.asarray(sim, dtype=np.float64)


def _soundex_score_matrix(values: list) -> np.ndarray:
    """NxN soundex match matrix."""
    codes = [jellyfish.soundex(v) if v is not None else None for v in values]
    return _exact_score_matrix(codes)


# ---------------------------------------------------------------------------
# PPRL (Privacy-Preserving Record Linkage) scoring
# ---------------------------------------------------------------------------

def _hex_to_bits(hex_str: str) -> np.ndarray:
    """Convert hex-encoded bloom filter to a numpy uint8 byte array."""
    return np.frombuffer(bytes.fromhex(hex_str), dtype=np.uint8)


def _dice_score_single(val_a: str, val_b: str) -> float:
    """Dice coefficient on two hex-encoded bloom filters."""
    bits_a = _hex_to_bits(val_a)
    bits_b = _hex_to_bits(val_b)
    intersection = np.unpackbits(np.bitwise_and(bits_a, bits_b)).sum()
    total = np.unpackbits(bits_a).sum() + np.unpackbits(bits_b).sum()
    return float(2.0 * intersection / total) if total > 0 else 0.0


def _jaccard_score_single(val_a: str, val_b: str) -> float:
    """Jaccard similarity on two hex-encoded bloom filters."""
    bits_a = _hex_to_bits(val_a)
    bits_b = _hex_to_bits(val_b)
    intersection = np.unpackbits(np.bitwise_and(bits_a, bits_b)).sum()
    union = np.unpackbits(np.bitwise_or(bits_a, bits_b)).sum()
    return float(intersection / union) if union > 0 else 0.0


def _dice_score_matrix(values: list) -> np.ndarray:
    """NxN Dice coefficient matrix on hex-encoded bloom filters.

    Uses vectorized numpy operations: unpack all bloom filters to bits,
    compute intersection via matrix multiply, and popcount via sum.
    """
    n = len(values)
    # Convert hex strings to bit matrix (n, filter_size_bits)
    bit_arrays = []
    for v in values:
        if v is not None:
            bit_arrays.append(np.unpackbits(_hex_to_bits(v)))
        else:
            bit_arrays.append(np.zeros(0, dtype=np.uint8))

    # Handle variable-length or empty arrays
    if not bit_arrays or len(bit_arrays[0]) == 0:
        return np.zeros((n, n))

    max_len = max(len(b) for b in bit_arrays)
    bit_matrix = np.zeros((n, max_len), dtype=np.float32)
    for i, b in enumerate(bit_arrays):
        if len(b) > 0:
            bit_matrix[i, :len(b)] = b

    # Intersection: dot product of bit vectors
    intersection = bit_matrix @ bit_matrix.T  # (n, n)

    # Popcount per vector
    popcounts = bit_matrix.sum(axis=1)  # (n,)

    # Dice: 2*|A&B| / (|A| + |B|)
    denom = popcounts[:, None] + popcounts[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        dice = np.where(denom > 0, 2.0 * intersection / denom, 0.0)

    return dice.astype(np.float64)


def _jaccard_score_matrix(values: list) -> np.ndarray:
    """NxN Jaccard similarity matrix on hex-encoded bloom filters."""
    n = len(values)
    bit_arrays = []
    for v in values:
        if v is not None:
            bit_arrays.append(np.unpackbits(_hex_to_bits(v)))
        else:
            bit_arrays.append(np.zeros(0, dtype=np.uint8))

    if not bit_arrays or len(bit_arrays[0]) == 0:
        return np.zeros((n, n))

    max_len = max(len(b) for b in bit_arrays)
    bit_matrix = np.zeros((n, max_len), dtype=np.float32)
    for i, b in enumerate(bit_arrays):
        if len(b) > 0:
            bit_matrix[i, :len(b)] = b

    intersection = bit_matrix @ bit_matrix.T
    popcounts = bit_matrix.sum(axis=1)
    # Union: |A| + |B| - |A&B|
    union = popcounts[:, None] + popcounts[None, :] - intersection
    with np.errstate(divide="ignore", invalid="ignore"):
        jaccard = np.where(union > 0, intersection / union, 0.0)

    return jaccard.astype(np.float64)


def _build_null_mask(values: list) -> np.ndarray:
    """NxN boolean mask — True where either value is null."""
    null_arr = np.array([v is None for v in values])
    return null_arr[:, None] | null_arr[None, :]


def find_fuzzy_matches(
    block_df: pl.DataFrame,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]] | None = None,
    pre_scored_pairs: list[tuple[int, int, float]] | None = None,
) -> list[tuple[int, int, float]]:
    """Find fuzzy matches within a block DataFrame.

    Uses vectorized rapidfuzz cdist for batch scoring, with early termination
    when exact fields make it mathematically impossible to reach the threshold.

    Args:
        block_df: Block DataFrame with __row_id__ and field columns.
        mk: Matchkey configuration with fields, weights, and threshold.
        exclude_pairs: Optional set of (min_id, max_id) pairs to skip.
        pre_scored_pairs: Optional pre-computed (id_a, id_b, score) pairs
            from ANN blocking. When set, skip NxN scoring.

    Returns:
        List of (row_id_a, row_id_b, score) tuples above threshold.
    """
    # find_fuzzy_matches requires mk.threshold + field weights/scorers set;
    # upstream config validation enforces this. Pyright sees the schema-level
    # Optional, so we narrow once here for clarity.
    assert mk.threshold is not None, "find_fuzzy_matches requires mk.threshold"
    mk_threshold: float = mk.threshold
    # Fast path: pre-scored pairs from ANN (skip NxN scoring)
    if pre_scored_pairs is not None:
        results = []
        for a, b, score in pre_scored_pairs:
            if score >= mk_threshold:
                pair_key = (min(a, b), max(a, b))
                if exclude_pairs and pair_key in exclude_pairs:
                    continue
                results.append((pair_key[0], pair_key[1], score))
        return results

    n = block_df.height
    if n < 2:
        return []

    row_ids = block_df["__row_id__"].to_list()

    # Separate exact (cheap), record_embedding, and fuzzy (expensive) fields
    exact_fields = [f for f in mk.fields if f.scorer == "exact" or f.scorer == "soundex_match"]
    record_emb_fields = [f for f in mk.fields if f.scorer == "record_embedding"]
    fuzzy_fields = [f for f in mk.fields if f.scorer not in ("exact", "soundex_match", "record_embedding")]

    # All scoring-path MatchkeyFields are upstream-validated to have weight set;
    # narrow with cast helper so the schema-level Optional doesn't poison every
    # sum(). Runtime behavior unchanged: if weight is None, the subsequent
    # arithmetic raises TypeError exactly as today.
    from typing import cast as _cast

    def _w(f: MatchkeyField) -> float:
        return _cast(float, f.weight)

    total_weight = sum(_w(f) for f in mk.fields)
    if total_weight == 0.0:
        return []

    # Phase 1: Score cheap fields (exact + soundex) and build null masks
    cheap_numerator = np.zeros((n, n))
    cheap_denominator = np.zeros((n, n))

    for f in exact_fields:
        values = _get_transformed_values(block_df, f)
        null_mask = _build_null_mask(values)
        valid = ~null_mask

        if f.scorer == "exact":
            scores = _exact_score_matrix(values)
        else:  # soundex_match
            scores = _soundex_score_matrix(values)

        cheap_numerator += scores * _w(f) * valid
        cheap_denominator += _w(f) * valid

    # Phase 2: Early termination check
    # For each pair, the maximum possible score is:
    #   (cheap_contribution + fuzzy_max_weight) / (cheap_denom + fuzzy_max_weight)
    # where fuzzy_max_weight assumes all fuzzy fields score 1.0
    fuzzy_total_weight = sum(_w(f) for f in fuzzy_fields) + sum(_w(f) for f in record_emb_fields)

    # If no fuzzy or record_embedding fields, just use cheap scores
    if not fuzzy_fields and not record_emb_fields:
        with np.errstate(divide="ignore", invalid="ignore"):
            combined = np.where(cheap_denominator > 0, cheap_numerator / cheap_denominator, 0.0)
    else:
        # Check which pairs can possibly reach threshold even if all fuzzy fields score 1.0
        max_possible_numerator = cheap_numerator + fuzzy_total_weight
        max_possible_denominator = cheap_denominator + fuzzy_total_weight

        with np.errstate(divide="ignore", invalid="ignore"):
            max_possible = np.where(
                max_possible_denominator > 0,
                max_possible_numerator / max_possible_denominator,
                0.0,
            )

        # Pairs that can't possibly reach threshold — mark them
        impossible = max_possible < mk_threshold

        # Phase 3: Score fuzzy fields with intra-field early termination
        fuzzy_numerator = np.zeros((n, n))
        fuzzy_denominator = np.zeros((n, n))

        all_expensive_fields = list(fuzzy_fields) + list(record_emb_fields)
        for f_idx, f in enumerate(all_expensive_fields):
            if f.scorer == "record_embedding":
                try:
                    scores = _record_embedding_score_matrix(
                        block_df, f.columns or [], model_name=f.model or "all-MiniLM-L6-v2",
                        column_weights=f.column_weights,
                    )
                except Exception:
                    logger.warning(
                        "Record embedding scorer failed for columns %s, falling back to token_sort",
                        f.columns, exc_info=True,
                    )
                    concat_values = []
                    for row in block_df.to_dicts():
                        parts = [str(row.get(c, "") or "") for c in (f.columns or [])]
                        concat_values.append(" ".join(parts))
                    scores = _fuzzy_score_matrix(concat_values, "token_sort")
                fuzzy_numerator += scores * _w(f)
                fuzzy_denominator += _w(f)
            else:
                values = _get_transformed_values(block_df, f)
                null_mask = _build_null_mask(values)
                valid = ~null_mask

                assert f.scorer is not None, "fuzzy field scorer must be set"
                scores = _fuzzy_score_matrix(values, f.scorer, model_name=f.model or "all-MiniLM-L6-v2")

                fuzzy_numerator += scores * _w(f) * valid
                fuzzy_denominator += _w(f) * valid

            # Intra-field early termination: if no pair can reach threshold
            # even with perfect scores on all remaining fields, stop early
            remaining_weight = sum(
                _w(all_expensive_fields[i])
                for i in range(f_idx + 1, len(all_expensive_fields))
            )
            if remaining_weight > 0:
                total_num = cheap_numerator + fuzzy_numerator
                total_den = cheap_denominator + fuzzy_denominator
                # Best case: remaining fields all score 1.0
                best_num = total_num + remaining_weight
                best_den = total_den + remaining_weight
                with np.errstate(divide="ignore", invalid="ignore"):
                    best_possible = np.where(best_den > 0, best_num / best_den, 0.0)
                # Apply existing impossible mask
                best_possible[impossible] = 0.0
                # Only check upper triangle
                best_upper = np.triu(best_possible, k=1)
                if np.max(best_upper) < mk_threshold:
                    break  # No pair can reach threshold, skip remaining fields

        # Combine cheap + fuzzy
        total_numerator = cheap_numerator + fuzzy_numerator
        total_denominator = cheap_denominator + fuzzy_denominator

        with np.errstate(divide="ignore", invalid="ignore"):
            combined = np.where(total_denominator > 0, total_numerator / total_denominator, 0.0)

        # Zero out impossible pairs (early termination)
        combined[impossible] = 0.0

    # Extract upper triangle pairs above threshold using numpy
    # Zero out lower triangle and diagonal
    upper = np.triu(combined, k=1)
    rows_idx, cols_idx = np.where(upper >= mk_threshold)

    if len(rows_idx) == 0:
        return []

    row_id_arr = np.array(row_ids)
    ids_a = row_id_arr[rows_idx]
    ids_b = row_id_arr[cols_idx]
    scores = upper[rows_idx, cols_idx]

    # v1.11: Apply negative-evidence penalty for weighted matchkeys.
    # NE is per-pair (not vectorized), applied only when mk.negative_evidence is set.
    if mk.negative_evidence:
        block_rows = block_df.to_dicts()
        ne_scores = []
        for i, j, s in zip(rows_idx, cols_idx, scores):
            row_a = block_rows[int(i)]
            row_b = block_rows[int(j)]
            ne_pair = {col: (row_a.get(col), row_b.get(col)) for col in row_a}
            penalty = _apply_negative_evidence(mk, ne_pair)
            final_s = max(0.0, float(s) - penalty)
            ne_scores.append(final_s)
        scores = ne_scores
        # Re-filter: only keep pairs whose adjusted score meets threshold
        if exclude_pairs is not None and len(exclude_pairs) > 0:
            results = []
            for a, b, s in zip(ids_a, ids_b, scores):
                if s < mk_threshold:
                    continue
                pair_key = (min(int(a), int(b)), max(int(a), int(b)))
                if pair_key not in exclude_pairs:
                    results.append((int(a), int(b), float(s)))
            return results
        return [(int(a), int(b), float(s)) for a, b, s in zip(ids_a, ids_b, scores)
                if s >= mk_threshold]

    if exclude_pairs is not None and len(exclude_pairs) > 0:
        results = []
        for a, b, s in zip(ids_a, ids_b, scores):
            pair_key = (min(int(a), int(b)), max(int(a), int(b)))
            if pair_key not in exclude_pairs:
                results.append((int(a), int(b), float(s)))
        return results

    return [(int(a), int(b), float(s)) for a, b, s in zip(ids_a, ids_b, scores)]


# ---------------------------------------------------------------------------
# Parallel block scoring
# ---------------------------------------------------------------------------

def _score_one_block(
    block: Any,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
) -> list[tuple[int, int, float]]:
    """Score a single block — safe to call from a thread."""
    block_df = block.df.collect()

    if across_files_only and source_lookup:
        sources_in_block = block_df["__source__"].unique().to_list()
        if len(sources_in_block) < 2:
            return []

    pairs = find_fuzzy_matches(
        block_df, mk,
        exclude_pairs=exclude_pairs,
        pre_scored_pairs=block.pre_scored_pairs,
    )

    if across_files_only and source_lookup:
        pairs = [
            (a, b, s) for a, b, s in pairs
            if source_lookup.get(a) != source_lookup.get(b)
        ]

    return pairs


def score_blocks_parallel(
    blocks: list,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    max_workers: int = 4,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int, float]]:
    """Score all blocks in parallel using threads.

    rapidfuzz.cdist releases the GIL, so threads provide real parallelism
    for the expensive fuzzy scoring. Blocks are independent — no shared
    mutable state during scoring.

    Args:
        blocks: List of BlockResult objects.
        mk: Matchkey configuration.
        matched_pairs: Set of already-matched (min_id, max_id) pairs.
        max_workers: Thread pool size (default 4).
        across_files_only: Filter to cross-source pairs only.
        source_lookup: Row ID to source name mapping.
        target_ids: For match mode — filter to target/ref cross pairs.

    Returns:
        All fuzzy pairs found across blocks.
    """
    if not blocks:
        return []

    # For small block counts, skip thread overhead
    if len(blocks) <= 2:
        all_pairs = []
        total_candidates = 0
        for block in blocks:
            block_df = block.df.collect()
            n = block_df.height
            total_candidates += n * (n - 1) // 2
            pairs = _score_one_block(
                block, mk, matched_pairs,
                across_files_only=across_files_only,
                source_lookup=source_lookup,
            )
            if target_ids is not None:
                pairs = [
                    (a, b, s) for a, b, s in pairs
                    if (a in target_ids) != (b in target_ids)
                ]
            all_pairs.extend(pairs)
            for a, b, _s in pairs:
                matched_pairs.add((min(a, b), max(a, b)))
        _emit_scoring_profile(all_pairs, mk.threshold, candidates_compared=total_candidates)  # pyright: ignore[reportArgumentType]  # caller ensures threshold set for fuzzy matchkeys
        return all_pairs

    # Snapshot exclude_pairs so threads see a frozen copy
    frozen_exclude = frozenset(matched_pairs)

    # Total candidate pairs across all blocks — computed cheaply by collecting
    # the LazyFrame.  Note: each block is collected here for counting, then
    # collected again inside _score_one_block.  Polars LazyFrames backed by
    # in-memory data re-collect in O(1), so this is acceptable overhead.
    # Overcounts when matched_pairs already covers some intra-block pairs;
    # acceptable approximation documented in ScoringProfile.candidates_compared.
    total_candidates = 0
    for block in blocks:
        try:
            n = block.df.collect().height
        except Exception:
            n = 0
        total_candidates += n * (n - 1) // 2

    all_pairs = []
    total_blocks = len(blocks)
    log_interval = max(total_blocks // 10, 1)  # log ~10 progress updates

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for i, block in enumerate(blocks):
            future = executor.submit(
                _score_one_block, block, mk, frozen_exclude,
                across_files_only, source_lookup,
            )
            future_to_idx[future] = i

        completed = 0
        for future in as_completed(future_to_idx):
            pairs = future.result()
            if target_ids is not None:
                pairs = [
                    (a, b, s) for a, b, s in pairs
                    if (a in target_ids) != (b in target_ids)
                ]
            all_pairs.extend(pairs)
            for a, b, _s in pairs:
                matched_pairs.add((min(a, b), max(a, b)))
            completed += 1
            if completed % log_interval == 0:
                logger.info(
                    "Scoring progress: %d/%d blocks (%d%%), %d pairs so far",
                    completed, total_blocks,
                    int(completed / total_blocks * 100),
                    len(all_pairs),
                )

    logger.info(
        "Parallel scoring: %d blocks, %d workers, %d pairs found",
        total_blocks, max_workers, len(all_pairs),
    )
    _emit_scoring_profile(all_pairs, mk.threshold, candidates_compared=total_candidates)  # pyright: ignore[reportArgumentType]  # caller ensures threshold set for fuzzy matchkeys
    return all_pairs


# ---------------------------------------------------------------------------
# Cross-encoder reranking
# ---------------------------------------------------------------------------

def rerank_top_pairs(
    pairs: list[tuple[int, int, float]],
    df: pl.DataFrame,
    mk: MatchkeyConfig,
) -> list[tuple[int, int, float]]:
    """Re-score borderline pairs with a pre-trained cross-encoder.

    Pairs within a band around the threshold (threshold +/- rerank_band)
    are re-scored using a cross-encoder model. Pairs outside the band
    keep their original scores. No training needed -- uses an off-the-shelf
    cross-encoder for zero-shot reranking.

    Args:
        pairs: All scored pairs (row_id_a, row_id_b, score).
        df: Full collected DataFrame with record data.
        mk: Matchkey config with rerank, rerank_model, rerank_band, threshold.

    Returns:
        Updated pairs list with reranked scores for borderline pairs.
    """
    if not mk.rerank or not pairs or mk.threshold is None:
        return pairs

    try:
        from sentence_transformers import (
            CrossEncoder,  # pyright: ignore[reportMissingImports]  # optional dep, ImportError caught below
        )
    except ImportError:
        logger.warning("Cross-encoder reranking unavailable: sentence-transformers not installed")
        return pairs

    band = mk.rerank_band
    lo = mk.threshold - band
    hi = mk.threshold + band

    # Identify borderline pairs
    borderline_idx = [i for i, (_, _, s) in enumerate(pairs) if lo <= s <= hi]
    if not borderline_idx:
        logger.info("Rerank: no pairs in band [%.2f, %.2f], skipping", lo, hi)
        return pairs

    # Build row lookup for serialization
    matchable_cols = [c for c in df.columns if not c.startswith("__")]
    row_lookup: dict[int, dict] = {}
    for row in df.select(["__row_id__"] + matchable_cols).to_dicts():
        row_lookup[row["__row_id__"]] = row

    # Serialize borderline pairs
    from goldenmatch.core.cross_encoder import serialize_record

    sentence_pairs = []
    for idx in borderline_idx:
        a, b, _ = pairs[idx]
        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})
        text_a = serialize_record(row_a, matchable_cols)
        text_b = serialize_record(row_b, matchable_cols)
        sentence_pairs.append((text_a, text_b))

    # Score with cross-encoder
    logger.info(
        "Rerank: scoring %d borderline pairs with %s",
        len(borderline_idx), mk.rerank_model,
    )
    model = CrossEncoder(mk.rerank_model)
    from goldenmatch.core.cross_encoder import score_pairs as ce_score_pairs

    ce_scores = ce_score_pairs(model, sentence_pairs)

    # Replace scores for borderline pairs
    result = list(pairs)
    for i, idx in enumerate(borderline_idx):
        a, b, _ = result[idx]
        result[idx] = (a, b, float(ce_scores[i]))

    # Re-filter by threshold
    result = [(a, b, s) for a, b, s in result if s >= mk.threshold]

    logger.info(
        "Rerank: %d pairs after reranking (was %d)",
        len(result), len(pairs),
    )
    return result
