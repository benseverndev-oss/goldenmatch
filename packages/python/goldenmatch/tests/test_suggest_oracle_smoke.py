"""Oracle smoke test: evaluate_dataset on the always-available synthetic dataset.

Guards:
- Skips when the native kernel is unavailable (graceful degradation path).
- Verifies the returned dict has all expected keys and a numeric baseline_f1.
"""
from __future__ import annotations

# Set BEFORE any goldenmatch import so the native-loader probe at collection
# time (in _suggest_available below, evaluated by the skipif) can't hit the
# Polars CPU-check WMI hang on Windows.
import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import math
import sys
from pathlib import Path

import pytest

# Make scripts/ importable
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _suggest_available() -> bool:
    """True when both the native suggest_config kernel AND the worktree engine
    surface (MatchEngine.from_dataframe) are present.

    The from_dataframe check skips (not errors) when this test runs against a
    stale installed package without the worktree on PYTHONPATH.
    """
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415
        nm = native_module()
        if nm is None or not hasattr(nm, "suggest_config"):
            return False
        from goldenmatch.tui.engine import MatchEngine  # noqa: PLC0415
        return hasattr(MatchEngine, "from_dataframe")
    except Exception:
        return False


@pytest.mark.skipif(
    not _suggest_available(),
    reason="native suggest_config kernel absent or requires worktree package "
           "(MatchEngine.from_dataframe missing)",
)
def test_evaluate_dataset_synthetic_returns_expected_keys():
    """evaluate_dataset('synthetic') returns a dict with expected keys + numeric baseline_f1."""
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    from scripts.suggest_quality.datasets import _synthetic  # noqa: PLC0415
    from scripts.suggest_quality.oracle import evaluate_dataset  # noqa: PLC0415

    loaded = _synthetic()
    assert loaded is not None, "synthetic dataset failed to load"
    df, gt_pairs = loaded

    result = evaluate_dataset("synthetic", df, gt_pairs)

    # All expected keys must be present
    expected_keys = {
        "name", "rows", "gt_pairs", "baseline_f1",
        "n_suggestions", "suggested_order_lifts",
        "convergence_final_f1", "convergence_steps",
        "native_available", "error",
    }
    assert expected_keys.issubset(result.keys()), (
        f"Missing keys: {expected_keys - result.keys()}"
    )

    # baseline_f1 must be numeric (not nan, not None)
    baseline_f1 = result["baseline_f1"]
    assert isinstance(baseline_f1, float), f"baseline_f1 type: {type(baseline_f1)}"
    assert not math.isnan(baseline_f1), "baseline_f1 is NaN (gt_pairs present, should be numeric)"
    assert 0.0 <= baseline_f1 <= 1.0, f"baseline_f1 out of range: {baseline_f1}"

    # rows must match df
    assert result["rows"] == df.height

    # gt_pairs must be non-negative
    assert result["gt_pairs"] >= 0

    # No error
    assert result["error"] is None, f"evaluate_dataset returned error: {result['error']}"
