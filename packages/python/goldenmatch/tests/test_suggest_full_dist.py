import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import sys
from pathlib import Path

import pytest

from goldenmatch.core.suggest import adapter as A

# Make scripts/ importable (suggest_quality gym loader + oracle + perturbations).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _suggest_available() -> bool:
    """True when both the native suggest_config kernel AND the worktree engine
    surface (MatchEngine.from_dataframe) are present. Mirrors the guard in
    test_suggest_oracle_smoke.py so these tests skip cleanly off the worktree."""
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415
        nm = native_module()
        if nm is None or not hasattr(nm, "suggest_config"):
            return False
        from goldenmatch.tui.engine import MatchEngine  # noqa: PLC0415
        return hasattr(MatchEngine, "from_dataframe")
    except Exception:
        return False


def _ncvr():
    """Deterministic, damage-capable NCVR-shaped corpus via the gym loader."""
    from scripts.suggest_quality.datasets import _ncvr_synthetic  # noqa: PLC0415
    df, _gt = _ncvr_synthetic()
    return df.with_row_index("__row_id__")


def _cfg():
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField, BlockingConfig, BlockingKeyConfig,
    )
    mk = MatchkeyConfig(name="person", type="weighted", threshold=0.85, fields=[
        MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
    ])
    return GoldenMatchConfig(matchkeys=[mk],
                             blocking=BlockingConfig(strategy="static",
                                                     keys=[BlockingKeyConfig(fields=["last_name"])]))

def test_full_dist_default_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_FULL_DIST", raising=False)
    assert A._full_dist_enabled() is False

def test_full_dist_on_when_1(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    assert A._full_dist_enabled() is True

def test_diagnostic_config_forces_all_thresholds_to_zero():
    cfg = _cfg()
    diag = A._zero_threshold_config(cfg)
    assert all(mk.threshold == 0.0 for mk in diag.get_matchkeys())
    # original untouched (immutability)
    assert cfg.get_matchkeys()[0].threshold == 0.85
    # blocking unchanged (candidate set must be identical)
    assert diag.blocking == cfg.blocking


@pytest.mark.skipif(
    not _suggest_available(),
    reason="native suggest_config kernel absent or requires worktree package "
           "(MatchEngine.from_dataframe missing)",
)
def test_full_dist_off_is_unchanged(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_FULL_DIST", raising=False)
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.suggest import review_config  # noqa: PLC0415
    from scripts.suggest_quality.oracle import _auto_configure_no_rerank  # noqa: PLC0415
    df = _ncvr()
    cfg = _auto_configure_no_rerank(df)
    sugg = review_config(df, cfg, verify=False)
    # Known-buggy current behavior: with threshold-filtered pairs only,
    # mass_above is always ~1.0, so the kernel only ever raises.
    assert all(s.kind == "raise_threshold" for s in sugg), \
        f"expected all raise_threshold, got {[s.kind for s in sugg]}"


@pytest.mark.skipif(
    not _suggest_available(),
    reason="native suggest_config kernel absent or requires worktree package "
           "(MatchEngine.from_dataframe missing)",
)
def test_full_dist_on_fires_lower_threshold_on_too_high(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.suggest import review_config  # noqa: PLC0415
    from scripts.suggest_quality.oracle import _auto_configure_no_rerank  # noqa: PLC0415
    from scripts.suggest_quality.perturbations import get as getp  # noqa: PLC0415
    df = _ncvr()
    ceil = _auto_configure_no_rerank(df)
    too_high = getp("threshold_too_high").apply(ceil)
    sugg = review_config(df, too_high, verify=False)
    kinds = {s.kind for s in sugg}
    assert "lower_threshold" in kinds, f"expected lower_threshold, got {kinds}"
