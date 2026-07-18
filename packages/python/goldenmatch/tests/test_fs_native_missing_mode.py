"""The native FS kernel only implements the "unobserved"/neutral missing
semantics. A matchkey whose auto-config picked `missing="disagree"` (#1834/#1851,
e.g. null-heavy historical_50k) must DECLINE the native path and score on numpy,
which honors both modes -- otherwise native scores nulls as neutral instead of
level-0-disagree and precision collapses (the #1869 regression on historical_50k
f1_probabilistic 0.83 -> 0.33)."""

from __future__ import annotations

import os

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField


def _mk(missing: str) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="p", type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.8),
            MatchkeyField(field="surname", scorer="jaro_winkler", levels=3,
                          partial_threshold=0.8),
        ],
        missing=missing, link_threshold=0.0,
    )


def _native_ready() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module
        mod = native_module()
        return bool(mod) and hasattr(mod, "score_block_pairs_fs")
    except Exception:
        return False


def test_disagree_mode_declines_native(monkeypatch):
    """`missing="disagree"` is not expressible by the kernel -> decline to numpy."""
    from goldenmatch.core import probabilistic as P

    assert P.fs_missing_mode(_mk("disagree")) == "disagree"
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    # Declined regardless of whether the native ext is present.
    assert P._fs_native_eligible(_mk("disagree")) is False


def test_unobserved_mode_stays_native_eligible(monkeypatch):
    """The default `unobserved`/neutral mode IS what the kernel implements."""
    from goldenmatch.core import probabilistic as P

    assert P.fs_missing_mode(_mk("unobserved")) == "unobserved"
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    if _native_ready():
        assert P._fs_native_eligible(_mk("unobserved")) is True


def test_env_disagree_override_also_declines(monkeypatch):
    """`GOLDENMATCH_FS_MISSING=disagree` (env override) also declines native."""
    from goldenmatch.core import probabilistic as P

    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MISSING", "disagree")
    try:
        assert P._fs_native_eligible(_mk("unobserved")) is False
    finally:
        os.environ.pop("GOLDENMATCH_FS_MISSING", None)
