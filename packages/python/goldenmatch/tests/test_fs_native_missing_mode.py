"""Native FS kernel missing-value semantics.

The kernel implements BOTH modes now: "unobserved"/neutral (a null field is
skipped) and "disagree" (#1834/#1851 -- a null field scores as level 0, evidence
AGAINST; auto-config picks it per-dataset for null-heavy data like
historical_50k). Disagree runs native only on wheels that expose the
`FS_SUPPORTS_MISSING_DISAGREE` capability (via the `missing_disagree=` kwarg);
OLDER wheels lack it and `_fs_native_eligible` declines them to the numpy path,
which honors both modes. Before the kernel gained disagree support, disagree
declined native unconditionally -- these tests now assert the capability-gated
contract instead.

The assertions are written as the CONTRACT relationship (eligible == the native
path is usable AND, for disagree, the kernel advertises the capability) so they
hold whether or not the native ext is present/enabled in the running lane.
"""

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


def _native_usable() -> bool:
    """The native FS path is both ENABLED (env gate) and PRESENT (symbol)."""
    from goldenmatch.core import probabilistic as P
    from goldenmatch.core._native_loader import native_module

    if not P._fs_native_enabled():
        return False
    mod = native_module()
    return bool(mod) and hasattr(mod, "score_block_pairs_fs")


def _disagree_supported() -> bool:
    from goldenmatch.core._native_loader import native_module

    mod = native_module()
    return bool(mod) and bool(getattr(mod, "FS_SUPPORTS_MISSING_DISAGREE", False))


def test_disagree_mode_native_eligible_when_kernel_supports_it(monkeypatch):
    """`missing="disagree"` runs native IFF the native path is usable AND the
    kernel exposes FS_SUPPORTS_MISSING_DISAGREE; older wheels decline to numpy."""
    from goldenmatch.core import probabilistic as P

    assert P.fs_missing_mode(_mk("disagree")) == "disagree"
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    expected = _native_usable() and _disagree_supported()
    assert P._fs_native_eligible(_mk("disagree")) is expected


def test_unobserved_mode_stays_native_eligible(monkeypatch):
    """The default `unobserved`/neutral mode IS what the kernel implements, so it
    is eligible exactly when the native path is usable."""
    from goldenmatch.core import probabilistic as P

    assert P.fs_missing_mode(_mk("unobserved")) == "unobserved"
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    assert P._fs_native_eligible(_mk("unobserved")) is _native_usable()


def test_env_disagree_override_matches_capability(monkeypatch):
    """`GOLDENMATCH_FS_MISSING=disagree` (env override) follows the same
    capability gate as a config-level disagree matchkey."""
    from goldenmatch.core import probabilistic as P

    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MISSING", "disagree")
    try:
        # The env override resolves the mode to disagree regardless of mk.missing.
        assert P.fs_missing_mode(_mk("unobserved")) == "disagree"
        expected = _native_usable() and _disagree_supported()
        assert P._fs_native_eligible(_mk("unobserved")) is expected
    finally:
        os.environ.pop("GOLDENMATCH_FS_MISSING", None)
