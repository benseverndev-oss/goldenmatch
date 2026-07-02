"""Reference-mode gate tests (docs/design/2026-07-01-rust-is-the-reference-roadmap.md).

Under GOLDENANALYSIS_NATIVE=auto, native runs wherever a component's kernel symbol
exists; pure-Python is the lossy fallback. _GATED_ON no longer governs auto.
"""
from __future__ import annotations

from goldenanalysis.core import _native_loader as nl


def test_auto_runs_native_when_symbol_present(monkeypatch):
    class FakeNative:
        histogram = staticmethod(lambda *a, **k: None)
        quantile = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENANALYSIS_NATIVE", raising=False)
    assert nl.native_enabled("histogram") is True
    assert nl.native_enabled("quantile") is True


def test_auto_falls_back_when_symbol_absent(monkeypatch):
    class FakeNativeNoQuantile:
        histogram = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNativeNoQuantile)
    monkeypatch.delenv("GOLDENANALYSIS_NATIVE", raising=False)
    assert nl.native_enabled("histogram") is True
    assert nl.native_enabled("quantile") is False  # symbol absent -> fallback


def test_unknown_component_is_fallback(monkeypatch):
    class FakeNative:
        histogram = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENANALYSIS_NATIVE", raising=False)
    assert nl.native_enabled("frame.row_count") is False


def test_env_zero_forces_fallback(monkeypatch):
    class FakeNative:
        histogram = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "0")
    assert nl.native_enabled("histogram") is False


def test_env_one_requires_native(monkeypatch):
    import pytest

    monkeypatch.setattr(nl, "_native", None)
    monkeypatch.setenv("GOLDENANALYSIS_NATIVE", "1")
    with pytest.raises(RuntimeError):
        nl.native_enabled("histogram")
