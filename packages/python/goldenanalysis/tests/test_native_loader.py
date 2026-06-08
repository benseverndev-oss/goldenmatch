"""Native-loader gate contract (Phase 1: pure fallback, _GATED_ON empty)."""

from __future__ import annotations

import pytest
from goldenanalysis.core import _native_loader as nl


def test_native_absent_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOLDENANALYSIS_NATIVE", raising=False)
    assert nl.native_module() is None
    assert nl.native_available() is False
    assert nl.native_enabled("anything") is False


def test_force_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "0")
    assert nl.native_enabled("anything") is False


def test_require_native_raises_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "1")
    # No kernel is built in Phase 1, so require-native must raise.
    if nl.native_module() is None:
        with pytest.raises(RuntimeError):
            nl.native_enabled("anything")


def test_gated_on_is_empty() -> None:
    assert nl._GATED_ON == frozenset()
