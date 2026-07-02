"""Reference-mode gate tests (docs/design/2026-07-01-rust-is-the-reference-roadmap.md).

Under GOLDENCHECK_NATIVE=auto, native runs wherever a component's kernel symbol
exists; pure-Python is the lossy fallback. _GATED_ON no longer governs auto.
"""
from __future__ import annotations

from goldencheck.core import _native_loader as nl


def test_auto_runs_native_when_symbol_present(monkeypatch):
    class FakeNative:
        benford_leading_digits = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENCHECK_NATIVE", raising=False)
    assert nl.native_enabled("benford") is True


def test_auto_falls_back_when_symbol_absent(monkeypatch):
    class FakeNativeNoBenford:
        composite_key_search = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNativeNoBenford)
    monkeypatch.delenv("GOLDENCHECK_NATIVE", raising=False)
    assert nl.native_enabled("benford") is False  # symbol absent -> fallback
    assert nl.native_enabled("composite_keys") is True


def test_unknown_component_is_fallback(monkeypatch):
    class FakeNative:
        benford_leading_digits = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENCHECK_NATIVE", raising=False)
    assert nl.native_enabled("does_not_exist") is False


def test_env_zero_forces_fallback(monkeypatch):
    class FakeNative:
        benford_leading_digits = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    assert nl.native_enabled("benford") is False


def test_env_one_requires_native(monkeypatch):
    import pytest

    monkeypatch.setattr(nl, "_native", None)
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    with pytest.raises(RuntimeError):
        nl.native_enabled("benford")
