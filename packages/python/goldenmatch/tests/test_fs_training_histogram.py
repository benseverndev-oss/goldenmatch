"""EMResult.training_score_histogram: the EM training sample's linear-score histogram."""

from __future__ import annotations

import numpy as np
import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.probabilistic import (
    EMResult,
    _otsu_split_from_counts,
    _otsu_threshold,
    train_em,
)


def _blocked_fixture():
    """200 rows (100 entities x 2 records) in zip blocks of 10 (5 entities each).

    900 blocked pairs. The 100 true duplicates agree on both names; every other pair
    disagrees on both. That gives two separated score masses, well past the calibrator's
    50-pair minimum.
    """
    from goldenmatch.core.blocker import build_blocks

    df = pl.DataFrame({
        "__row_id__": list(range(200)),
        "zip": [f"Z{i // 10}" for i in range(200)],
        "first_name": [f"F{i // 2}" for i in range(200)],
        "last_name": [f"L{(i // 2) % 37}" for i in range(200)],
    })
    mk = MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="exact", levels=2),
            MatchkeyField(field="last_name", scorer="exact", levels=2),
        ],
    )
    blocks = build_blocks(
        df.lazy(),
        BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    return df, mk, blocks


def test_train_em_stores_the_training_histogram():
    df, mk, blocks = _blocked_fixture()
    em = train_em(df, mk, n_sample_pairs=1000, max_iterations=10,
                  blocks=blocks, blocking_fields=["zip"])
    hist = em.training_score_histogram
    assert hist is not None
    assert len(hist["counts"]) == 100
    assert sum(hist["counts"]) > 50
    assert hist["lo"] < hist["hi"]
    assert sum(1 for c in hist["counts"] if c) >= 2, "both score masses must be present"


def test_histogram_round_trips_through_json_and_is_optional(tmp_path):
    hist = {"lo": -3.0, "hi": 5.0, "counts": [1] * 100}
    em = EMResult(
        m_probs={}, u_probs={}, match_weights={}, converged=True, iterations=1,
        proportion_matched=0.1, training_score_histogram=hist,
    )
    path = str(tmp_path / "model.json")
    em.save_json(path)
    assert EMResult.load_json(path).training_score_histogram == hist

    bare = EMResult(m_probs={}, u_probs={}, match_weights={}, converged=True,
                    iterations=1, proportion_matched=0.1)
    assert "training_score_histogram" not in bare.to_dict()
    bare_path = str(tmp_path / "bare.json")
    bare.save_json(bare_path)
    assert EMResult.load_json(bare_path).training_score_histogram is None


def test_otsu_on_counts_equals_otsu_on_scores():
    rng = np.random.default_rng(7)
    scores = np.concatenate([rng.normal(0.2, 0.05, 500), rng.normal(0.8, 0.05, 200)]).clip(0, 1)
    counts, _ = np.histogram(scores, bins=100, range=(0.0, 1.0))
    assert _otsu_split_from_counts(counts) == _otsu_threshold(scores)
