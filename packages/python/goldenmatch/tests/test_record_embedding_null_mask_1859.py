"""#1859: the vectorized weighted scorer must mask an unobserved
``record_embedding`` field out of BOTH the numerator and the denominator, like
every other field type -- otherwise a row missing all its embedding columns
still contributes the field's full weight to the denominator, diluting the score
(the #1856 shape on a first-class weighted scorer).

The real ``record_embedding`` scorer needs an embedding model (torch/HF), which
hangs in CI here, so these tests stub ``_record_embedding_score_matrix`` with a
fixed matrix and check the masking arithmetic at the ``find_fuzzy_matches`` level.
"""
from __future__ import annotations

import numpy as np
import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import scorer as scorer_mod
from goldenmatch.core.scorer import find_fuzzy_matches


def _mk() -> MatchkeyConfig:
    # exact name (cheap field, always agrees below) + a record_embedding field.
    return MatchkeyConfig(
        name="mk",
        type="weighted",
        threshold=0.4,
        fields=[
            MatchkeyField(field="name", scorer="exact", weight=1.0),
            MatchkeyField(scorer="record_embedding", columns=["bio"], weight=1.0),
        ],
    )


def _df() -> pl.DataFrame:
    # All names agree (exact -> 1.0). row2 has a NULL bio (unobserved on the
    # record_embedding field); rows 0/1 both have a bio.
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2],
            "name": ["alice", "alice", "alice"],
            "bio": ["x", "y", None],
        }
    )


def test_null_record_embedding_is_masked_out(monkeypatch):
    # Stub the embedding similarity to a constant 0.0 (the model says "no
    # similarity") so the arithmetic is unambiguous.
    monkeypatch.setattr(
        scorer_mod,
        "_record_embedding_score_matrix",
        lambda block_df, cols, **kw: np.zeros((block_df.height, block_df.height), dtype=np.float32),
    )
    pairs = find_fuzzy_matches(_df(), _mk())
    scores = {(min(a, b), max(a, b)): s for a, b, s in pairs}

    # Pair (0,1): both bio present -> emb (0.0) counts. score = (1*1 + 0*1)/(1+1) = 0.5.
    assert (0, 1) in scores
    assert abs(scores[(0, 1)] - 0.5) < 1e-6, scores

    # Pairs with row2 (NULL bio): emb masked out. score = name only = 1/1 = 1.0.
    # Without the fix these would dilute to 0.5 (emb wrongly in the denominator).
    assert abs(scores[(0, 2)] - 1.0) < 1e-6, scores
    assert abs(scores[(1, 2)] - 1.0) < 1e-6, scores


def test_clean_data_unchanged(monkeypatch):
    # No all-null embedding rows -> the mask is all-True, so behavior is
    # byte-identical to before the fix (a pure correctness guard, no regression).
    monkeypatch.setattr(
        scorer_mod,
        "_record_embedding_score_matrix",
        lambda block_df, cols, **kw: np.full(
            (block_df.height, block_df.height), 0.6, dtype=np.float32
        ),
    )
    df = pl.DataFrame(
        {"__row_id__": [0, 1], "name": ["alice", "alice"], "bio": ["x", "y"]}
    )
    pairs = find_fuzzy_matches(df, _mk())
    scores = {(min(a, b), max(a, b)): s for a, b, s in pairs}
    # (1*1 + 0.6*1) / (1 + 1) = 0.8
    assert abs(scores[(0, 1)] - 0.8) < 1e-6, scores
