from goldenpipe.core import _native_loader as L


def test_planner_enabled_when_symbol_present(monkeypatch):
    class FakeNative:
        def resolve_json(self, s): ...
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", FakeNative())
    assert L.native_enabled("planner") is True


def test_force_off(monkeypatch):
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "0")
    monkeypatch.setattr(L, "_native", object())
    assert L.native_enabled("planner") is False


def test_require_raises_when_absent(monkeypatch):
    import pytest
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "1")
    monkeypatch.setattr(L, "_native", None)
    with pytest.raises(RuntimeError):
        L.native_enabled("planner")


def test_auto_disabled_when_symbol_missing(monkeypatch):
    class Bare:  # native present but no resolve_json symbol
        pass
    monkeypatch.setenv("GOLDENPIPE_NATIVE", "auto")
    monkeypatch.setattr(L, "_native", Bare())
    assert L.native_enabled("planner") is False
