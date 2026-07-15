"""Wiring tests for the diagnostics / issue-reporter (anti-spam discipline)."""
from __future__ import annotations

from typing import Any

import pytest
from goldenmatch.core import _diagnostics_report as R
from goldenmatch.core import diagnostics as D


@pytest.fixture(autouse=True)
def reset_diagnostics_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GOLDEN_DIAGNOSTICS", raising=False)
    R.reset()
    yield
    R.reset()


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    real = R.report_anomaly

    def fake(c: str, s: str, **k: Any) -> str | None:
        calls.append((c, s))
        return real(c, s, **k)

    monkeypatch.setattr(R, "report_anomaly", fake)
    return calls


def test_guard_entrypoint_prompts_on_unexpected(monkeypatch: pytest.MonkeyPatch):
    calls = _spy(monkeypatch)

    @D.guard_entrypoint("dedupe", "dedupe_df raised an unexpected error")
    def boom():
        raise RuntimeError("simulated kernel crash")

    with pytest.raises(RuntimeError, match="simulated kernel crash"):
        boom()
    assert calls and calls[0][0] == "dedupe"


@pytest.mark.parametrize("exc", [ValueError("bad col"), FileNotFoundError("nope"), KeyError("k")])
def test_guard_entrypoint_silent_on_user_errors(monkeypatch: pytest.MonkeyPatch, exc: Exception):
    calls = _spy(monkeypatch)

    @D.guard_entrypoint("dedupe", "x")
    def boom():
        raise exc

    with pytest.raises(type(exc)):
        boom()
    assert not calls  # user-input errors never prompt an issue


def test_guard_entrypoint_silent_on_by_design_refusal(monkeypatch: pytest.MonkeyPatch):
    calls = _spy(monkeypatch)

    class ByDesignRefusal(Exception):
        pass

    # a by-design exception listed in the expected set passes through silently
    monkeypatch.setattr(D, "_expected_exceptions", lambda: (ByDesignRefusal,))

    @D.guard_entrypoint("dedupe", "x")
    def refuse():
        raise ByDesignRefusal("refused by design")

    with pytest.raises(ByDesignRefusal):
        refuse()
    assert not calls  # a designed refusal is not a bug


def test_real_by_design_exceptions_are_in_expected_set():
    # Guard the taxonomy: the controller's refuse + the config-lint error must
    # be classified as expected (never prompt an issue).
    exp = D._expected_exceptions()
    names = {e.__name__ for e in exp}
    assert {"ControllerNotConfidentError", "ConfigLintError"} <= names


def test_guard_entrypoint_survives_broken_diagnostics(monkeypatch: pytest.MonkeyPatch):
    # Diagnostics is never load-bearing: even if the reporter's internals blow
    # up, the entrypoint's own exception is what propagates -- unchanged.
    def _explode(*a: Any, **k: Any) -> str:
        raise OSError("diagnostics internals down")

    monkeypatch.setattr(R, "issue_url", _explode)
    monkeypatch.setattr(R, "environment_report", _explode)

    @D.guard_entrypoint("dedupe", "x")
    def boom():
        raise RuntimeError("the real failure")

    with pytest.raises(RuntimeError, match="the real failure"):
        boom()


def test_native_import_anomaly_reports_only_broken_install(monkeypatch: pytest.MonkeyPatch):
    from goldenmatch.core import _native_loader as nl
    calls = _spy(monkeypatch)

    # broken install (non-ModuleNotFoundError) -> prompt
    monkeypatch.setattr(nl, "_NATIVE_IMPORT_ERROR", ImportError("undefined symbol: foo"))
    monkeypatch.setattr(nl, "_IMPORT_ANOMALY_REPORTED", False)
    nl._maybe_report_import_anomaly()
    assert any(c == "native-import" for c, _ in calls)

    # plain 'not installed' -> no prompt
    calls.clear()
    R.reset()
    monkeypatch.setattr(nl, "_NATIVE_IMPORT_ERROR", None)
    monkeypatch.setattr(nl, "_IMPORT_ANOMALY_REPORTED", False)
    nl._maybe_report_import_anomaly()
    assert not calls


def test_wheel_skew_prompts_when_symbol_missing(monkeypatch: pytest.MonkeyPatch):
    from goldenmatch.core import _native_loader as nl
    calls = _spy(monkeypatch)

    # A hot-path component fell back AND its symbol is absent from the wheel.
    summary = nl.NativeDispatchSummary(
        available=True,
        mode="auto",
        components={"field_scoring": {"native": 0, "fallback": 3}},
        ran_native=False,
        hot_path_exercised=True,
        hot_path_native=False,
    )
    monkeypatch.setattr(nl, "_has_symbol", lambda c: False)  # symbol missing -> skew
    assert nl.warn_if_slow_path(summary) is True
    assert any(c == "native-wheel-skew" for c, _ in calls)


def test_slow_path_without_skew_does_not_prompt(monkeypatch: pytest.MonkeyPatch):
    from goldenmatch.core import _native_loader as nl
    calls = _spy(monkeypatch)
    summary = nl.NativeDispatchSummary(
        available=True,
        mode="auto",
        components={"field_scoring": {"native": 0, "fallback": 3}},
        ran_native=False,
        hot_path_exercised=True,
        hot_path_native=False,
    )
    monkeypatch.setattr(nl, "_has_symbol", lambda c: True)  # symbol PRESENT -> legit fallback
    nl.warn_if_slow_path(summary)
    assert not any(c == "native-wheel-skew" for c, _ in calls)
