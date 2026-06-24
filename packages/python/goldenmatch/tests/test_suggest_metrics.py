"""Unit tests for scripts/suggest_quality/metrics.py — pure functions, no data.

Sign-convention reminder (from metrics.py docstring):
  rank_correlation([highest_lift_first, ...]) -> +1.0
  rank_correlation([lowest_lift_first, ...])  -> -1.0
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# scripts/ lives at the repo root; make sure it's importable regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parents[5]  # …/goldenmatch/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.suggest_quality.metrics import convergence, rank_correlation, suggester_precision


# ─── rank_correlation ────────────────────────────────────────────────────────

class TestRankCorrelation:
    def test_perfect_descending_order_gives_plus_one(self):
        """Lifts already in descending order: suggester is perfect -> +1.0."""
        lifts = [0.3, 0.2, 0.1]
        rho = rank_correlation(lifts)
        assert abs(rho - 1.0) < 1e-9, f"expected +1.0, got {rho}"

    def test_perfect_ascending_order_gives_minus_one(self):
        """Lifts in ascending order: worst-first suggester -> -1.0."""
        lifts = [0.1, 0.2, 0.3]
        rho = rank_correlation(lifts)
        assert abs(rho - (-1.0)) < 1e-9, f"expected -1.0, got {rho}"

    def test_empty_returns_nan(self):
        assert math.isnan(rank_correlation([]))

    def test_single_element_returns_nan(self):
        assert math.isnan(rank_correlation([0.5]))

    def test_two_elements_descending_gives_plus_one(self):
        lifts = [0.2, 0.1]
        rho = rank_correlation(lifts)
        assert abs(rho - 1.0) < 1e-9

    def test_two_elements_ascending_gives_minus_one(self):
        lifts = [0.1, 0.2]
        rho = rank_correlation(lifts)
        assert abs(rho - (-1.0)) < 1e-9

    def test_all_equal_lifts_returns_nan(self):
        # Zero variance -> Spearman undefined
        lifts = [0.1, 0.1, 0.1]
        rho = rank_correlation(lifts)
        assert math.isnan(rho)

    def test_mixed_positive_negative_lifts(self):
        # Descending with negatives still -> +1.0
        lifts = [0.1, 0.0, -0.1]
        rho = rank_correlation(lifts)
        assert abs(rho - 1.0) < 1e-9

    def test_sign_convention_documented(self):
        """Explicit sanity: rank0 = best, rank_last = worst -> perfect ordering -> +1."""
        # rank position 0 has lift 0.5, rank position 2 has lift 0.1
        # Negated: 0 -> -0.5, 1 -> -0.2, 2 -> -0.1
        # Ranks [0,1,2] vs neg_lifts [-0.5,-0.2,-0.1]: ascending vs ascending -> +1
        lifts = [0.5, 0.2, 0.1]
        assert abs(rank_correlation(lifts) - 1.0) < 1e-9


# ─── suggester_precision ─────────────────────────────────────────────────────

class TestSuggesterPrecision:
    def test_all_positive_gives_one(self):
        assert suggester_precision([0.1, 0.2, 0.3]) == 1.0

    def test_all_negative_gives_zero(self):
        assert suggester_precision([-0.1, -0.2]) == 0.0

    def test_empty_gives_one(self):
        assert suggester_precision([]) == 1.0

    def test_mixed_two_thirds(self):
        # 0.1 (ok), -0.2 (bad), 0.0 (ok = no regression) -> 2/3
        result = suggester_precision([0.1, -0.2, 0.0])
        assert abs(result - 2 / 3) < 1e-9, f"expected 2/3, got {result}"

    def test_zero_lift_counts_as_non_harmful(self):
        assert suggester_precision([0.0]) == 1.0

    def test_single_positive(self):
        assert suggester_precision([0.05]) == 1.0

    def test_single_negative(self):
        assert suggester_precision([-0.05]) == 0.0


# ─── convergence ─────────────────────────────────────────────────────────────

class TestConvergence:
    def test_empty_steps(self):
        result = convergence([])
        assert result == {"final_f1": 0.0, "steps": 0, "improved": False}

    def test_single_step(self):
        result = convergence([("raise_threshold:mk1", 0.75)])
        assert result["final_f1"] == pytest.approx(0.75)
        assert result["steps"] == 1
        assert result["improved"] is True

    def test_multiple_steps(self):
        trail = [
            ("raise_threshold:mk1", 0.72),
            ("swap_scorer:mk1:name", 0.78),
            ("add_ne:email", 0.80),
        ]
        result = convergence(trail)
        assert result["final_f1"] == pytest.approx(0.80)
        assert result["steps"] == 3
        assert result["improved"] is True

    def test_improved_false_only_when_empty(self):
        # Even a single step (even if small gain) counts as improved
        result = convergence([("some_suggestion", 0.0)])
        assert result["improved"] is True

    def test_final_f1_is_last_not_max(self):
        # Convergence may regress after the cap; final_f1 = the last step
        trail = [("a", 0.9), ("b", 0.85)]
        result = convergence(trail)
        assert result["final_f1"] == pytest.approx(0.85)
