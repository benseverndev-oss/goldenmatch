"""FS-native reference-mode tests (docs/design/2026-07-01-rust-is-the-reference-roadmap.md).

The native FS kernel is now the authoritative FS scorer by default; the numpy
vectorized path is the reproducible fallback via GOLDENMATCH_FS_NATIVE=0.
"""
from __future__ import annotations

import goldenmatch.core._native_loader as nl
from goldenmatch.core import probabilistic as p


def test_fs_native_authoritative_by_default(monkeypatch) -> None:
    # env unset -> native FS is ON when block_scoring native is available.
    monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
    monkeypatch.setattr(nl, "native_enabled", lambda component: True)
    assert p._fs_native_enabled() is True


def test_fs_native_force_off(monkeypatch) -> None:
    for val in ("0", "false", "no", "off", "disabled"):
        monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", val)
        monkeypatch.setattr(nl, "native_enabled", lambda component: True)
        assert p._fs_native_enabled() is False, val


def test_fs_native_falls_back_when_block_scoring_unavailable(monkeypatch) -> None:
    # default-on, but if block_scoring native isn't available the numpy path runs.
    monkeypatch.delenv("GOLDENMATCH_FS_NATIVE", raising=False)
    monkeypatch.setattr(nl, "native_enabled", lambda component: False)
    assert p._fs_native_enabled() is False


def test_fs_native_explicit_on_still_works(monkeypatch) -> None:
    # back-compat: =1 still turns it on.
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    monkeypatch.setattr(nl, "native_enabled", lambda component: True)
    assert p._fs_native_enabled() is True
