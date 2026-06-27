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
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
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
def test_full_dist_on_does_not_collapse_threshold_on_too_high(monkeypatch):
    """After the right-anchored dip fix (2026-06-25), the dip on the NCVR-synthetic
    `threshold_too_high` shape lands at the high-side valley (~0.875, the trough
    below the true-match mode), NOT the 0.04 left-tail sliver the buggy global-min
    dip returned.

    On this corpus the perturbation raises the threshold to 0.90, which is already
    within DIP_MIN_GAP (0.05) of the 0.875 valley, so the kernel correctly emits NO
    threshold suggestion rather than the destructive `lower_threshold -> 0.04` it
    used to fire (recorded at raw recovery -1231.6% in the prior full-dist findings).

    This test pins the FIX: no suggestion may lower the `fuzzy_match` threshold into
    the left tail. (Originally `test_full_dist_on_fires_lower_threshold_on_too_high`,
    which asserted the buggy `lower_threshold` KIND fires; that expectation encoded
    the bug this plan removes -- updated per the plan's "written justification" rule.)
    """
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.suggest import review_config  # noqa: PLC0415

    from scripts.suggest_quality.oracle import _auto_configure_no_rerank  # noqa: PLC0415
    from scripts.suggest_quality.perturbations import get as getp  # noqa: PLC0415
    df = _ncvr()
    ceil = _auto_configure_no_rerank(df)
    too_high = getp("threshold_too_high").apply(ceil)
    sugg = review_config(df, too_high, verify=False)
    # No suggestion may collapse the threshold into the low-score tail.
    for s in sugg:
        if s.kind == "lower_threshold":
            proposed = float(s.proposed_value)
            assert proposed >= 0.10, (
                f"lower_threshold must not collapse into the left-tail sliver, "
                f"got proposed_value={proposed} (suggestion {s.id})"
            )


@pytest.mark.skipif(
    not _suggest_available(),
    reason="native suggest_config kernel absent or requires worktree package "
           "(MatchEngine.from_dataframe missing)",
)
def test_full_dist_on_lowers_to_high_side_valley_when_threshold_far_above(monkeypatch):
    """ACTIVE end-to-end assertion of the right-anchored dip fix through the
    Python boundary (review_config).

    The `..._does_not_collapse...` test above is a regression guard whose loop
    body is vacuous on this corpus (the `threshold_too_high` perturbation lands
    the threshold at 0.90, already within DIP_MIN_GAP (0.05) of the 0.875 valley,
    so NO `lower_threshold` fires). This test forces the dip rule to fire by
    setting the threshold to 0.98 -- 0.105 ABOVE the valley, well beyond
    DIP_MIN_GAP -- so a `lower_threshold` MUST be emitted, and asserts its
    proposed value is the HIGH-side valley (>= 0.75; observed 0.88), NOT the
    0.04 left-tail sliver the buggy global-min dip used to return.

    This is the positive counterpart to the Rust unit tests: it proves the fix
    survives the full marshaling path (diagnostic-run -> Arrow batches ->
    suggest_config kernel -> Suggestion) and not just in `cargo test`.
    """
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    from goldenmatch.core.suggest import review_config  # noqa: PLC0415

    from scripts.suggest_quality.oracle import _auto_configure_no_rerank  # noqa: PLC0415
    df = _ncvr()
    cfg = _auto_configure_no_rerank(df)
    # Set the primary weighted/fuzzy matchkey threshold WELL above the 0.875
    # valley (> DIP_MIN_GAP) so the dip rule is guaranteed to fire.
    primary = next(
        mk for mk in cfg.get_matchkeys()
        if mk.type in ("weighted", "fuzzy") and mk.threshold is not None
    )
    primary.threshold = 0.98
    sugg = review_config(df, cfg, verify=False)
    lowers = [s for s in sugg if s.kind == "lower_threshold"]
    assert lowers, (
        "expected a lower_threshold suggestion at threshold 0.98 "
        f"(dip valley ~0.875), got kinds {[s.kind for s in sugg]}"
    )
    for s in lowers:
        proposed = float(s.proposed_value)
        # High-side valley (~0.88), NOT the 0.04 left-tail sliver.
        assert proposed >= 0.75, (
            f"lower_threshold must target the high-side valley (>= 0.75), "
            f"got proposed_value={proposed} (suggestion {s.id})"
        )
