"""GoldenDB block scorer (backend='gpu'). **WORK IN PROGRESS.**

Conforms to the same contract as :func:`goldenmatch.core.scorer.score_blocks_parallel`
(``(blocks, mk, matched_pairs, ...) -> list[(row_id_a, row_id_b, score)]``) so it
drops into the pipeline via ``_get_block_scorer`` when ``config.backend == "gpu"``.

Per block it encodes each matchkey field to a matrix, computes per-field cosine via
a JAX matmul, and combines them with the GA2M weighted-average (exact attribution,
monotone). It scores WITHIN the existing blocks -- cross-block ANN recall (Stage A
in the design doc) is not wired yet, so recall is whatever the blocker produces.

See :mod:`goldenmatch.core.goldendb` for the full work-in-progress caveat list.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.goldendb import WIP_BANNER
from goldenmatch.core.goldendb._combine import combine_matrices
from goldenmatch.core.goldendb._encode import char_ngram_hashed, cosine_matrix
from goldenmatch.core.scorer import (
    _build_null_mask,
    _exact_score_matrix,
    _get_transformed_values,
)

logger = logging.getLogger(__name__)

_warned = False
_EXACT_SCORERS = frozenset({"exact"})


def _warn_once() -> None:
    global _warned
    if not _warned:
        logger.warning("%s", WIP_BANNER)
        _warned = True


def _field_sim_matrix(block_df, field: MatchkeyField) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(sim[N,N], valid[N,N])`` for one matchkey field.

    Exact-scorer fields use the hash-grouped exact matrix; everything else uses
    char-ngram cosine via the JAX matmul. ``valid`` is 0.0 where either value is
    null so a field only contributes where both sides have data.
    """
    values = _get_transformed_values(block_df, field)
    valid = (~_build_null_mask(values)).astype(np.float32)
    scorer = field.scorer
    if scorer in _EXACT_SCORERS:
        sim = _exact_score_matrix(values).astype(np.float32)
    else:
        # char-ngram embed -> cosine (the matmul hot path). Covers
        # jaro_winkler/levenshtein/token_sort/qgram/dice/jaccard/soundex etc.
        # in one continuous kernel (NOT byte-identical to those scorers -- this
        # is the experimental matrix approximation).
        mat = char_ngram_hashed(values)
        sim = cosine_matrix(mat)
    return sim, valid


def find_matches_gpu(
    block_df,
    mk: MatchkeyConfig,
    exclude_pairs: set | frozenset | None = None,
) -> list[tuple[int, int, float]]:
    """Score one block via the matrix path. Mirrors
    :func:`goldenmatch.core.scorer.find_fuzzy_matches`' return contract.
    """
    if mk.threshold is None:
        raise ValueError("find_matches_gpu requires mk.threshold")
    threshold = float(mk.threshold)

    n = block_df.height
    if n < 2:
        return []

    fields = list(mk.fields)
    weights = np.array(
        [float(f.weight) if f.weight is not None else 0.0 for f in fields],
        dtype=np.float64,
    )
    if weights.sum() == 0.0:
        return []

    if mk.negative_evidence:
        logger.warning(
            "GoldenDB backend (WIP): negative_evidence is configured but NOT yet "
            "applied by the matrix path; scoring without it."
        )

    sim_list: list[np.ndarray] = []
    valid_list: list[np.ndarray] = []
    for f in fields:
        sim, valid = _field_sim_matrix(block_df, f)
        sim_list.append(sim)
        valid_list.append(valid)

    sim_stack = np.stack(sim_list, axis=0)        # [K, N, N]
    valid_stack = np.stack(valid_list, axis=0)    # [K, N, N]
    score, _attribution = combine_matrices(sim_stack, weights, valid_stack)

    # Upper triangle above threshold.
    upper = np.triu(score, k=1)
    rows_idx, cols_idx = np.where(upper >= threshold)
    if len(rows_idx) == 0:
        return []

    row_ids = block_df["__row_id__"].to_list()
    results: list[tuple[int, int, float]] = []
    for i, j in zip(rows_idx, cols_idx):
        a = int(row_ids[int(i)])
        b = int(row_ids[int(j)])
        lo, hi = (a, b) if a <= b else (b, a)
        if exclude_pairs and (lo, hi) in exclude_pairs:
            continue
        results.append((lo, hi, float(upper[i, j])))
    return results


def score_blocks_gpu(
    blocks: list,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    max_workers: int | None = None,  # noqa: ARG001 - accepted for contract parity
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    **kwargs: Any,  # noqa: ARG001 - tolerate ray key-mode kwargs (store_path, signature)
) -> list[tuple[int, int, float]]:
    """Score all blocks via the matrix path. Drop-in for ``score_blocks_parallel``.

    Runs sequentially (the JAX matmul already vectorises the per-block NxN work;
    threading buys little here and keeps the WIP path simple). Pairs are
    canonicalised ``(min, max)``, filtered against ``matched_pairs``, and -- in
    match mode -- restricted to target/reference cross pairs.
    """
    _warn_once()
    if not blocks:
        return []

    all_pairs: list[tuple[int, int, float]] = []
    exclude: frozenset = frozenset(matched_pairs)
    for block in blocks:
        block_df = block.df.collect()

        if across_files_only and source_lookup:
            if "__source__" in block_df.columns:
                if block_df["__source__"].n_unique() < 2:
                    continue

        pairs = find_matches_gpu(block_df, mk, exclude_pairs=exclude)

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
        all_pairs.extend(pairs)

    return all_pairs
