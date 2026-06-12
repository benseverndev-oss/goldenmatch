"""Native-loader gate contract (pure fallback; histogram/quantile gated since P4)."""

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


def test_gated_on_holds_the_measured_primitives() -> None:
    # histogram + quantile joined _GATED_ON after the P4 measured flip (5.8-9.9x on
    # Linux, byte-identical parity). A new primitive joins only after the same gates.
    assert nl._GATED_ON == frozenset({"histogram", "quantile"})
