"""Native-dispatch telemetry on the result object (#1048, #957).

Covers the per-run ``NativeDispatchSummary`` builder, the slow-path WARNING
("never silently eat the slow path"), and that ``dedupe_df`` / ``match_df``
attach the summary so callers can CONFIRM native dispatch instead of inferring
it from wall-clock.
"""
from __future__ import annotations

import logging

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.core import _native_loader as nl


@pytest.fixture(autouse=True)
def _isolate_dispatch_log():
    """Each test starts from clean per-process dispatch + warn-once state."""
    nl.reset_native_dispatch_log()
    nl.reset_slow_path_warned()
    yield
    nl.reset_native_dispatch_log()
    nl.reset_slow_path_warned()


# ── summarize_native_dispatch ───────────────────────────────────────────


def test_summary_booleans_from_native_dispatch():
    nl._record_dispatch("field_scoring", True)
    nl._record_dispatch("field_scoring", True)
    s = nl.summarize_native_dispatch()
    assert s.ran_native is True
    assert s.hot_path_exercised is True
    assert s.hot_path_native is True
    assert s.hot_path_native_calls == 2
    assert s.hot_path_fallback_calls == 0


def test_summary_fallback_flips_hot_path_native_off():
    nl._record_dispatch("field_scoring", True)
    nl._record_dispatch("field_scoring", False)  # one block fell back
    s = nl.summarize_native_dispatch()
    assert s.hot_path_exercised is True
    assert s.hot_path_native is False  # any fallback => not fully native
    assert s.hot_path_native_calls == 1
    assert s.hot_path_fallback_calls == 1


def test_summary_baseline_delta_scopes_to_this_run():
    nl._record_dispatch("field_scoring", True)  # belongs to a prior run
    baseline = nl.native_dispatch_report()
    nl._record_dispatch("field_scoring", False)  # this run
    s = nl.summarize_native_dispatch(baseline=baseline)
    # Only the post-baseline dispatch is counted.
    assert s.components["field_scoring"] == {"native": 0, "fallback": 1}


def test_summary_non_hot_path_component_not_counted_as_hot():
    nl._record_dispatch("clustering", False)
    s = nl.summarize_native_dispatch()
    assert s.hot_path_exercised is False  # clustering is not the scoring hot path
    assert s.slow_path_active() is False


def test_summary_str_is_readable():
    nl._record_dispatch("field_scoring", True)
    assert "field_scoring" in str(nl.summarize_native_dispatch())


# ── warn_if_slow_path ───────────────────────────────────────────────────


def test_warn_fires_when_available_but_fell_back(monkeypatch, caplog):
    monkeypatch.setattr(nl, "native_available", lambda: True)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)  # auto
    nl._record_dispatch("field_scoring", False)
    s = nl.summarize_native_dispatch()
    assert s.slow_path_active() is True
    with caplog.at_level(logging.WARNING):
        assert nl.warn_if_slow_path(s, once_key="t") is True
    assert any("pure-Python fallback" in r.message for r in caplog.records)
    # once_key de-dupes: a second call with the same key is silent.
    assert nl.warn_if_slow_path(s, once_key="t") is False


def test_no_warn_when_fully_native(monkeypatch):
    monkeypatch.setattr(nl, "native_available", lambda: True)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    nl._record_dispatch("block_scoring", True)
    s = nl.summarize_native_dispatch()
    assert s.slow_path_active() is False
    assert nl.warn_if_slow_path(s, once_key="x") is False


def test_no_warn_when_forced_python(monkeypatch):
    monkeypatch.setattr(nl, "native_available", lambda: True)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")  # explicit user choice
    nl._record_dispatch("block_scoring", False)
    s = nl.summarize_native_dispatch()
    assert s.mode == "0"
    assert s.slow_path_active() is False


def test_no_warn_when_native_unavailable(monkeypatch):
    monkeypatch.setattr(nl, "native_available", lambda: False)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    nl._record_dispatch("field_scoring", False)
    s = nl.summarize_native_dispatch()
    assert s.available is False
    assert s.slow_path_active() is False  # nothing to warn about


# ── dedupe_df / match_df integration ────────────────────────────────────


def _fuzzy_frame(n: int = 400) -> pl.DataFrame:
    """A blocked fuzzy shape large enough to drive the cdist score-matrix path
    (the hot path that records ``field_scoring``)."""
    import random

    random.seed(1)
    first = ["John", "Jon", "Alice", "Alyce", "Bob", "Rob", "Mary", "Mari"]
    last = ["Smith", "Smyth", "Lee", "Lea", "Jones", "Jonas"]
    rows = [
        {
            "id": i,
            "name": f"{random.choice(first)} {random.choice(last)}",
            "zip": f"{random.randint(1000, 1010)}",
        }
        for i in range(n)
    ]
    return pl.DataFrame(rows)


def test_dedupe_df_attaches_native_summary():
    res = gm.dedupe_df(_fuzzy_frame(), fuzzy={"name": 0.85}, blocking=["zip"])
    assert res.native is not None
    # A fuzzy matchkey over a blocked frame exercises the scoring hot path. The
    # legacy per-block path records `field_scoring` (the cdist field-score matrix);
    # the DEFAULT bucket scorer records `block_scoring`. Either means the native
    # scoring hot path was exercised.
    assert res.native.hot_path_exercised is True
    assert (
        "field_scoring" in res.native.components
        or "block_scoring" in res.native.components
    )


def test_match_df_attaches_native_summary():
    target = pl.DataFrame({"id": [1, 2], "name": ["John Smith", "Mary Jones"]})
    reference = pl.DataFrame({"id": [10, 11], "name": ["Jon Smith", "Mary Jones"]})
    res = gm.match_df(target, reference, fuzzy={"name": 0.8})
    assert res.native is not None
    assert isinstance(res.native.ran_native, bool)
