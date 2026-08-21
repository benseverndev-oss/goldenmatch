"""Tests for the Layer-1 decision-geometry probe helpers.

Covers ONLY the pure, model-free pieces (pair mining determinism/gating + the
three probe estimators on synthetic reps) so the suite is box-safe -- no GGUF, no
llama.cpp, no network. The heavy ``extract_reps``/``main`` path is exercised
manually against the pinned model (see the module docstring)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from interp.decision_geometry import (  # noqa: E402
    mine_probe_pairs,
    probe_held_out_direction,
    probe_linear_separability,
    probe_low_rank,
)


def _toy_gold(n_clusters: int = 40, per: int = 3) -> tuple[list[int], list[str]]:
    """n_clusters clusters of `per` rows each; surname key = cluster % 7 so several
    distinct clusters share a phonetic key (=> hard negatives exist)."""
    gold, surn = [], []
    for c in range(n_clusters):
        for _ in range(per):
            gold.append(c)
            surn.append(f"S{c % 7}")
    return gold, surn


class TestMinePairs:
    def test_deterministic_given_seed(self):
        gold, surn = _toy_gold()
        a = mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0)
        b = mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0)
        assert a == b

    def test_seed_changes_selection(self):
        gold, surn = _toy_gold()
        a = mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0)
        b = mine_probe_pairs(gold, surn, 20, negatives="hard", seed=1)
        assert a != b

    def test_balanced_and_labeled(self):
        gold, surn = _toy_gold()
        pairs = mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0)
        assert len(pairs) == 40
        assert sum(t for *_, t in pairs) == 20  # 20 match
        assert sum(1 - t for *_, t in pairs) == 20  # 20 non-match

    def test_matches_are_same_cluster(self):
        gold, surn = _toy_gold()
        for a, b, t in mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0):
            if t == 1:
                assert gold[a] == gold[b]

    def test_hard_negatives_share_surname_key_but_differ_cluster(self):
        gold, surn = _toy_gold()
        for a, b, t in mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0):
            if t == 0:
                assert gold[a] != gold[b]
                assert surn[a] == surn[b]  # the hardness condition

    def test_random_negatives_differ_cluster(self):
        gold, surn = _toy_gold()
        pairs = mine_probe_pairs(gold, surn, 20, negatives="random", seed=0)
        assert all(gold[a] != gold[b] for a, b, t in pairs if t == 0)

    def test_no_duplicate_pairs(self):
        gold, surn = _toy_gold()
        pairs = mine_probe_pairs(gold, surn, 20, negatives="hard", seed=0)
        keys = [(min(a, b), max(a, b)) for a, b, _ in pairs]
        assert len(keys) == len(set(keys))

    def test_raises_when_insufficient(self):
        gold, surn = _toy_gold(n_clusters=5, per=2)
        with pytest.raises(ValueError):
            mine_probe_pairs(gold, surn, 1000, negatives="hard", seed=0)

    def test_rejects_bad_args(self):
        gold, surn = _toy_gold()
        with pytest.raises(ValueError):
            mine_probe_pairs(gold, surn, 0, seed=0)
        with pytest.raises(ValueError):
            mine_probe_pairs(gold, surn[:-1], 5, seed=0)
        with pytest.raises(ValueError):
            mine_probe_pairs(gold, surn, 5, negatives="bogus", seed=0)


def _separable_reps(n: int = 240, dim: int = 16, gap: float = 6.0, seed: int = 0):
    """Two Gaussian blobs whose separation lives in a rank-1 CORRELATED subspace
    (a shared latent factor loaded onto several dims). This mirrors how real
    representations behave -- the concept is a low-dim direction spread across
    correlated features -- so standardize-then-PCA keeps it as the dominant PC
    (a lone bimodal axis would be flattened by standardization)."""
    rng = np.random.default_rng(seed)
    y = np.array([1] * (n // 2) + [0] * (n // 2))
    latent = np.where(y == 1, 1.0, -1.0) * (gap / 2.0)
    X = rng.standard_normal((n, dim))
    loadings = rng.standard_normal(min(4, dim))
    for c, w in enumerate(loadings):
        X[:, c] += w * latent + rng.standard_normal(n) * 0.1  # correlated signal block
    return X, y


class TestProbes:
    def test_linear_separability_high_on_separable(self):
        X, y = _separable_reps(gap=4.0)
        assert probe_linear_separability(X, y) > 0.9

    def test_linear_separability_chance_on_noise(self):
        rng = np.random.default_rng(1)
        X = rng.standard_normal((120, 32))
        y = np.array([1] * 60 + [0] * 60)
        assert probe_linear_separability(X, y) < 0.75  # near chance, no real signal

    def test_held_out_direction_recovers_the_axis(self):
        X, y = _separable_reps(gap=4.0)
        mean_auc, std_auc = probe_held_out_direction(X, y, seed=0)
        assert mean_auc > 0.9
        assert std_auc >= 0.0

    def test_low_rank_plateaus_for_one_dim_signal(self):
        # signal on a single axis -> even top-1 PC should already be strong,
        # and adding PCs must not collapse accuracy.
        X, y = _separable_reps()
        low = probe_low_rank(X, y, ks=(1, 2, 4, 8))
        assert set(low) == {1, 2, 4, 8}
        assert low[8] > 0.85
        assert low[1] > 0.75  # the low-rank concept is recoverable at rank 1

    def test_low_rank_skips_k_over_dim(self):
        X, y = _separable_reps(dim=4)
        low = probe_low_rank(X, y, ks=(1, 2, 8, 16))
        assert set(low).issubset({1, 2})  # 8, 16 > dim are skipped
