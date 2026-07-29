"""Tests for the pure published-SOTA-F1 lookup table (SP3 Task 2).

Feeds the zero-shot eval's display-only comparison column (mirrors
scripts/er_matcher/test_gpu_tiers.py conventions -- pure stdlib logic, no
torch/network)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sota_baselines import sota_for  # noqa: E402


def test_has_unseen_benchmarks_with_both_baselines():
    for k in ("walmart_amazon", "beer", "fodors_zagats", "itunes_amazon"):
        e = sota_for(k)
        assert e is not None
        assert 0.0 < e["deepmatcher_f1"] <= 1.0
        assert 0.0 < e["ditto_f1"] <= 1.0
        assert e["source"]


def test_ditto_beats_deepmatcher_on_these():
    for k in ("walmart_amazon", "beer"):
        assert sota_for(k)["ditto_f1"] > sota_for(k)["deepmatcher_f1"]


def test_unknown_returns_none():
    assert sota_for("nonexistent") is None
