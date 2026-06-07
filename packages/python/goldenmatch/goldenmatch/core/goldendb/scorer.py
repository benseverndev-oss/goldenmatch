"""GoldenDB block scorer (backend='gpu'). **WORK IN PROGRESS.**

Conforms to the same contract as :func:`goldenmatch.core.scorer.score_blocks_parallel`
(``(blocks, mk, matched_pairs, ...) -> list[(row_id_a, row_id_b, score)]``) so it
drops into the pipeline via ``_get_block_scorer`` when ``config.backend == "gpu"``.

Two scoring paths per block:

* **dense** (small blocks, ``n <= ANN_THRESHOLD``): build the full per-field ``[N, N]``
  cosine via a JAX matmul and combine. Simple, exact, quadratic in memory.
* **recall** (large blocks): Stage A coarse-vector top-k ANN shortlist
  (:mod:`goldenmatch.core.goldendb.recall`) followed by a vectorised Stage B that
  scores only the shortlisted candidate pairs -- the spec's "N^2 cost lives only in
  Stage A" path. With ``k >= n-1`` the recall path is exactly the dense path.

Cross-*block* recall is still future work: this scores within the blocks the
blocker produced. See :mod:`goldenmatch.core.goldendb` for the full WIP caveats.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.goldendb import WIP_BANNER
from goldenmatch.core.goldendb._combine import combine_matrices
from goldenmatch.core.goldendb._encode import char_ngram_hashed, cosine_matrix
from goldenmatch.core.goldendb.recall import coarse_encode, topk_candidates
from goldenmatch.core.scorer import (
    _apply_negative_evidence,
    _build_null_mask,
    _exact_score_matrix,
    _get_transformed_values,
)

logger = logging.getLogger(__name__)

_warned = False
_EXACT_SCORERS = frozenset({"exact"})

# Block size above which Stage A ANN recall replaces the dense NxN path, and the
# default per-record neighbour count for that recall. Tunable via env.
ANN_THRESHOLD = int(os.environ.get("GOLDENMATCH_GOLDENDB_ANN_THRESHOLD", "4096"))
ANN_K = int(os.environ.get("GOLDENMATCH_GOLDENDB_ANN_K", "20"))


def _warn_once() -> None:
    global _warned
    if not _warned:
        logger.warning("%s", WIP_BANNER)
        _warned = True


class _Field:
    """Prepared per-field data, computed once per block."""

    __slots__ = ("scorer", "weight", "is_exact", "emb", "values", "null")

    def __init__(self, scorer: str, weight: float, is_exact: bool,
                 emb: np.ndarray | None, values: list, null: np.ndarray):
        self.scorer = scorer
        self.weight = weight
        self.is_exact = is_exact
        self.emb = emb            # [N, dim] for fuzzy fields, else None
        self.values = values      # transformed values list
        self.null = null          # [N] bool, True where value is null


def _prep_fields(block_df, fields: list[MatchkeyField]) -> list[_Field]:
    prepped: list[_Field] = []
    for f in fields:
        values = _get_transformed_values(block_df, f)
        null = np.array([v is None for v in values])
        is_exact = f.scorer in _EXACT_SCORERS
        emb = None if is_exact else char_ngram_hashed(values)
        prepped.append(
            _Field(
                scorer=f.scorer or "",
                weight=float(f.weight) if f.weight is not None else 0.0,
                is_exact=is_exact,
                emb=emb,
                values=values,
                null=null,
            )
        )
    return prepped


def _dense_pairs(prepped: list[_Field], threshold: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dense NxN path. Returns (rows_idx, cols_idx, scores) for the upper triangle
    above threshold (index space, not row-id space)."""
    sim_list, valid_list = [], []
    for fld in prepped:
        if fld.is_exact:
            sim = _exact_score_matrix(fld.values).astype(np.float32)
        else:
            sim = cosine_matrix(fld.emb)
        valid = (~(fld.null[:, None] | fld.null[None, :])).astype(np.float32)
        sim_list.append(sim)
        valid_list.append(valid)
    weights = np.array([fld.weight for fld in prepped], dtype=np.float64)
    score, _attr = combine_matrices(np.stack(sim_list), weights, np.stack(valid_list))
    upper = np.triu(score, k=1)
    rows_idx, cols_idx = np.where(upper >= threshold)
    return rows_idx, cols_idx, upper[rows_idx, cols_idx]


def _recall_pairs(
    prepped: list[_Field], threshold: float, k: int, min_sim: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Stage A ANN recall + vectorised Stage B scoring on the shortlist.

    Returns (rows_idx, cols_idx, scores) in index space for candidate pairs whose
    combined score is >= threshold.
    """
    fuzzy = [fld for fld in prepped if not fld.is_exact and fld.emb is not None]
    if not fuzzy:
        # No vectors to recall on -- fall back to dense (exact-only blocks).
        return _dense_pairs(prepped, threshold)

    coarse = coarse_encode([fld.emb for fld in fuzzy],
                           np.array([fld.weight for fld in fuzzy]))
    cand = topk_candidates(coarse, k=k, min_sim=min_sim)
    if not cand:
        empty = np.array([], dtype=int)
        return empty, empty, np.array([], dtype=np.float32)

    I = np.array([c[0] for c in cand])
    J = np.array([c[1] for c in cand])
    num = np.zeros(len(cand), dtype=np.float64)
    den = np.zeros(len(cand), dtype=np.float64)
    for fld in prepped:
        validk = (~(fld.null[I] | fld.null[J])).astype(np.float64)
        if fld.is_exact:
            varr = np.array(fld.values, dtype=object)
            simk = (varr[I] == varr[J]).astype(np.float64)
        else:
            # cosine on the candidate subset = row-wise dot of L2-normalised embeddings
            simk = np.einsum("pd,pd->p", fld.emb[I], fld.emb[J])
            simk = np.clip(simk, 0.0, 1.0)
        num += fld.weight * simk * validk
        den += fld.weight * validk
    score = np.where(den > 0.0, num / np.where(den > 0.0, den, 1.0), 0.0)
    keep = score >= threshold
    return I[keep], J[keep], score[keep].astype(np.float32)


def find_matches_gpu(
    block_df,
    mk: MatchkeyConfig,
    exclude_pairs: set | frozenset | None = None,
    *,
    use_recall: bool | None = None,
    k: int | None = None,
    min_sim: float = 0.0,
) -> list[tuple[int, int, float]]:
    """Score one block via the matrix path. Mirrors
    :func:`goldenmatch.core.scorer.find_fuzzy_matches`' return contract.

    ``use_recall`` forces (True) / disables (False) the Stage A ANN recall path;
    ``None`` (default) auto-selects by block size (``> ANN_THRESHOLD``). ``k`` is
    the per-record neighbour count for recall (default :data:`ANN_K`).
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

    prepped = _prep_fields(block_df, fields)

    if use_recall is None:
        use_recall = n > ANN_THRESHOLD
    if k is None:
        k = ANN_K

    if use_recall:
        rows_idx, cols_idx, scores = _recall_pairs(prepped, threshold, k, min_sim)
    else:
        rows_idx, cols_idx, scores = _dense_pairs(prepped, threshold)

    if len(rows_idx) == 0:
        return []

    # v1.11 negative evidence: subtract per-pair penalty when an NE field
    # disagrees, then re-threshold. Reuses the canonical scorer helper so the
    # semantics match the production path (final = max(0, score - penalty)).
    if mk.negative_evidence:
        block_rows = block_df.to_dicts()
        adjusted = []
        for i, j, s in zip(rows_idx, cols_idx, scores):
            ra = block_rows[int(i)]
            rb = block_rows[int(j)]
            ne_pair = {col: (ra.get(col), rb.get(col)) for col in ra}
            penalty = _apply_negative_evidence(mk, ne_pair)
            adjusted.append(max(0.0, float(s) - penalty))
        scores = np.asarray(adjusted, dtype=np.float32)
        keep = scores >= threshold
        rows_idx, cols_idx, scores = rows_idx[keep], cols_idx[keep], scores[keep]
        if len(rows_idx) == 0:
            return []

    row_ids = block_df["__row_id__"].to_list()
    results: list[tuple[int, int, float]] = []
    for i, j, s in zip(rows_idx, cols_idx, scores):
        a = int(row_ids[int(i)])
        b = int(row_ids[int(j)])
        lo, hi = (a, b) if a <= b else (b, a)
        if exclude_pairs and (lo, hi) in exclude_pairs:
            continue
        results.append((lo, hi, float(s)))
    return results


def resolve_dataset_gpu(
    df,
    mk: MatchkeyConfig,
    k: int | None = None,
    min_sim: float = 0.0,
    exclude_pairs: set | frozenset | None = None,
) -> list[tuple[int, int, float]]:
    """Blocker-free matrix-native resolution over an ENTIRE dataset.

    Runs Stage A ANN recall across all records (no blocking key required) and
    Stage B GA2M scoring on the shortlist -- the spec's "primitive join operator is
    approximate" path. Because recall is similarity-based rather than key-based, it
    finds duplicates that a blocking key would separate (e.g. ``Catherine`` vs
    ``Katherine`` under a first-letter key). ``df`` must carry ``__row_id__``.

    **WORK IN PROGRESS** -- brute-force top-k recall (good to ~1e5-1e6 rows per the
    design doc); a true GPU-ANN index is the next step for larger datasets.
    """
    _warn_once()
    return find_matches_gpu(
        df, mk, exclude_pairs=exclude_pairs, use_recall=True, k=k, min_sim=min_sim,
    )


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

    Runs sequentially (the JAX matmul already vectorises the per-block work).
    Pairs are canonicalised ``(min, max)``, filtered against ``matched_pairs``,
    and -- in match mode -- restricted to target/reference cross pairs.
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
