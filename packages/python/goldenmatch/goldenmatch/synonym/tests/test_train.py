from __future__ import annotations

import json

import numpy as np

from goldenmatch.synonym import train as T


def test_features_unit_range():
    f = T.pair_features("amoxicillin", "amoxicillin")
    assert f.shape == (6,)
    assert all(0.0 <= float(x) <= 1.0 for x in f)
    # identical strings -> jaccard2 / jw / prefix all 1.0
    assert f[0] == 1.0 and f[2] == 1.0


def test_train_reproducible():
    assert np.allclose(T.train_default(seed=0), T.train_default(seed=0))


def test_committed_weights_match_retrain():
    committed = np.asarray(json.loads(T._MODEL.read_text(encoding="utf-8"))["weights"])
    assert np.allclose(committed, T.train_default(seed=0), atol=1e-6)
