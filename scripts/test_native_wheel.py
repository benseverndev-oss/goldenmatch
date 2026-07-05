"""Unit tests for the native published-wheel drift advisory. Box-safe: uses a stub
module, no real wheel/build. Run: python -m pytest scripts/test_native_wheel.py -q"""
import importlib.util
import pathlib
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "check_native_wheel", pathlib.Path(__file__).parent / "check_native_wheel.py")
mod = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


def test_public_callables_filters():
    stub = types.SimpleNamespace(
        connected_components=lambda: 0,
        ExcludeSet=type("ExcludeSet", (), {}),   # a class is a public callable export
        _private=lambda: 0,
        __version__="1",
        DATA=42,                                  # non-callable
    )
    assert mod._public_callables(stub) == {"connected_components", "ExcludeSet"}


def test_lag_computation():
    assert mod.lag({"a", "b"}, {"a"}, set()) == {"b"}
    assert mod.lag({"a", "b"}, {"a"}, {"b"}) == set()          # allow subtracts
    assert mod.lag({"a"}, {"a", "extra"}, set()) == set()      # wheel exporting more is fine


def test_run_fails_loud_on_zero_references(monkeypatch):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: set())
    assert mod.run("goldenmatch") == 2   # zero refs => fail loud, not falsely green


def test_run_fails_loud_when_wheel_cannot_be_introspected(monkeypatch):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: {"connected_components"})
    def boom(_name): raise ModuleNotFoundError("no goldenmatch_native")
    monkeypatch.setattr(mod, "wheel_exports", boom)
    assert mod.run("goldenmatch") == 2   # can't introspect => fail loud


def test_run_warns_but_exits_zero_on_lag(monkeypatch, capsys):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: {"old_sym", "new_sym"})
    monkeypatch.setattr(mod, "wheel_exports", lambda _n: {"old_sym"})   # wheel lacks new_sym
    monkeypatch.setattr(mod._ns, "load_allow", lambda _p: set())
    rc = mod.run("goldenmatch")
    out = capsys.readouterr().out
    assert rc == 0                       # advisory: warn, don't fail
    assert "new_sym" in out and "republish" in out.lower()


def test_run_clean_when_wheel_covers_all(monkeypatch, capsys):
    monkeypatch.setattr(mod._ns, "scan_references", lambda *a, **k: {"a", "b"})
    monkeypatch.setattr(mod, "wheel_exports", lambda _n: {"a", "b", "c"})
    monkeypatch.setattr(mod._ns, "load_allow", lambda _p: set())
    assert mod.run("goldenmatch") == 0
    assert "up to date" in capsys.readouterr().out
