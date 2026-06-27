"""Gym smoke test: run_catalog demonstrates real recovery by a built-rule perturbation.

Guards:
- Skips when the native kernel is unavailable (graceful degradation path).
- Verifies that at least one built-rule perturbation causes measurable damage
  AND that the unsupervised convergence recovers via the expected rule.
"""
from __future__ import annotations

# Set BEFORE any goldenmatch import so the native-loader probe at collection
# time (in _suggest_available below, evaluated by the skipif) can't hit the
# Polars CPU-check WMI hang on Windows.
import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

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
    os.environ.get("PYTEST_XDIST_WORKER") is not None,
    reason="heavy native-kernel gym smoke crashes a parallel xdist worker under "
           "`-n auto` (resource contention, not a deterministic segfault — the same "
           "kernel runs clean serially in the suggest-quality lane). Runs serially / "
           "locally; gym recovery is also covered by the suggest-quality bench lane.",
)
@pytest.mark.skipif(
    not _suggest_available(),
    reason="native suggest_config kernel absent or requires worktree package "
           "(MatchEngine.from_dataframe missing)",
)
def test_gym_recovers_a_builtin_rule_perturbation():
    """run_catalog shows the gym mechanically works end to end.

    Asserts at least one built-rule perturbation causes measurable damage AND
    that the RAW path (verify=False, raw kernel rule capability) recovers via
    the expected rule.  The LIVE path (verify=True, production) may genuinely
    recover ~0 if the health-proxy self-verification suppresses the fix -- that
    is a real finding the gym reports honestly, not a broken gym, so we do NOT
    require live recovery > 0.
    """
    os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    from scripts.suggest_quality.datasets import REGISTRY  # noqa: PLC0415
    from scripts.suggest_quality.gym import run_catalog  # noqa: PLC0415
    from scripts.suggest_quality.perturbations import CATALOG  # noqa: PLC0415

    # Pick datasets where a built-rule perturbation can cause measurable damage.
    names = {"synthetic", "ncvr_synthetic"}
    datasets = [d for d in REGISTRY if d.name in names]
    built = [p for p in CATALOG if p.builds_on_existing_rule]

    assert datasets, "neither synthetic nor ncvr_synthetic found in REGISTRY"
    assert built, "no built-rule perturbations found in CATALOG"

    records = run_catalog(datasets, built)

    # Diagnostic: print a live-vs-raw table so the finding is visible on every run.
    print("\n--- gym records (live = production, raw = diagnostic) ---")
    for r in records:
        if r.get("status") == "ok":
            print(
                f"{r['dataset']:16s} {r['name']:24s} "
                f"ceiling={r['f1_ceiling']:.4f} degraded={r['f1_degraded']:.4f} "
                f"rec_live={r['recovery_pct_live']:+.4f} (fired={r['expected_rule_fired_live']}) "
                f"rec_raw={r['recovery_pct_raw']:+.4f} (fired={r['expected_rule_fired_raw']}) "
                f"gap={r['verification_gap']:+.4f}"
            )
        else:
            print(f"{r['dataset']:16s} {r['name']:24s} status={r['status']}")

    ok = [r for r in records if r.get("status") == "ok"]
    assert ok, (
        "no built-rule perturbation caused measurable damage on the chosen datasets. "
        f"All statuses: {[(r['dataset'], r['name'], r['status']) for r in records]}"
    )

    # The gym + rules function end to end: the RAW path recovers via the
    # expected rule on at least one damaging perturbation.
    assert any(
        r["recovery_pct_raw"] > 0 and r["expected_rule_fired_raw"] for r in ok
    ), (
        "gym + rules did not mechanically recover via the expected rule on the "
        f"raw path. ok records: {ok}"
    )
