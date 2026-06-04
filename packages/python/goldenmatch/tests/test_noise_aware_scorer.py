"""Tests for opt-in noise-aware scorer selection (#662).

When GOLDENMATCH_NOISE_AWARE_SCORERS=1, auto-config upgrades a token_sort
assignment on corruption-prone free-text col_types (address, string) to the
noise-aware target scorer (provisional: jaro_winkler). Default-off; never
overrides a non-token_sort scorer; never steals a short code from qgram.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _noise_aware_scorer,
    _noise_aware_target_scorer,
    _noise_aware_scorers_enabled,
    build_matchkeys,
)


@pytest.mark.parametrize("col_type", ["address", "string"])
def test_helper_upgrades_token_sort_when_enabled(monkeypatch, col_type):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "1")
    assert _noise_aware_scorer(col_type, "token_sort") == _noise_aware_target_scorer()


@pytest.mark.parametrize("col_type", ["address", "string"])
def test_helper_noop_when_disabled(monkeypatch, col_type):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "0")  # kill-switch
    assert _noise_aware_scorer(col_type, "token_sort") == "token_sort"


def test_helper_never_overrides_non_token_sort(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "1")
    assert _noise_aware_scorer("address", "ensemble") == "ensemble"
    assert _noise_aware_scorer("name", "ensemble") == "ensemble"


def test_helper_ignores_non_noise_prone_types(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "1")
    assert _noise_aware_scorer("description", "token_sort") == "token_sort"


def test_target_scorer_env_override(monkeypatch):
    """Benchmark-only override so the harness sweeps scorers without code edits."""
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "1")
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_TARGET", "ensemble")
    assert _noise_aware_scorer("address", "token_sort") == "ensemble"


def test_default_is_on(monkeypatch):
    """#662 Component 3: default flipped ON after benchmark. Pins the committed
    default so it can't silently drift back."""
    monkeypatch.delenv("GOLDENMATCH_NOISE_AWARE_SCORERS", raising=False)
    assert _noise_aware_scorers_enabled() is True


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "enabled"])
def test_enabled_accepts_house_truthy_values(monkeypatch, val):
    """The gate matches the module's case-insensitive value-set convention
    (GOLDENMATCH_AUTOCONFIG_LLM), so `=true` doesn't silently no-op."""
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", val)
    assert _noise_aware_scorers_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "disabled", "DISABLED"])
def test_enabled_rejects_falsy_values(monkeypatch, val):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", val)
    assert _noise_aware_scorers_enabled() is False


@pytest.mark.parametrize("val", ["", "1", "true", "enabled", "anything"])
def test_enabled_on_unless_explicit_killswitch(monkeypatch, val):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", val)
    assert _noise_aware_scorers_enabled() is True


def _address_profile() -> ColumnProfile:
    return ColumnProfile(
        name="res_street_address", dtype="str", col_type="address",
        confidence=0.9, null_rate=0.05, cardinality_ratio=0.8, avg_len=24.0,
    )


def _scorer_for(matchkeys, field_name: str):
    for mk in matchkeys:
        for f in mk.fields:
            if f.field == field_name:
                return f.scorer
    return None


def test_build_matchkeys_default_now_upgrades(monkeypatch):
    """Default-ON: address gets jaro_winkler with no env set."""
    monkeypatch.delenv("GOLDENMATCH_NOISE_AWARE_SCORERS", raising=False)
    mks = build_matchkeys([_address_profile()])
    assert _scorer_for(mks, "res_street_address") == "jaro_winkler"


def test_build_matchkeys_killswitch_keeps_token_sort(monkeypatch):
    """Kill-switch restores the legacy token_sort."""
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "0")
    mks = build_matchkeys([_address_profile()])
    assert _scorer_for(mks, "res_street_address") == "token_sort"


def test_build_matchkeys_flag_upgrades_to_target(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "1")
    mks = build_matchkeys([_address_profile()])
    assert _scorer_for(mks, "res_street_address") == _noise_aware_target_scorer()


def _short_code_profile() -> ColumnProfile:
    return ColumnProfile(
        name="sku", dtype="str", col_type="string", confidence=0.9,
        null_rate=0.0, cardinality_ratio=0.95, avg_len=7.0,
        sample_values=["A1B2C3", "X9Y8Z7", "Q4W5E6", "M3N2B1"],
    )


def test_short_code_keeps_qgram_even_with_flag(monkeypatch):
    """Ordering guard: the qgram short-code guard runs BEFORE the noise swap, so
    a code-like string column is routed to qgram and the swap no-ops on it."""
    monkeypatch.setenv("GOLDENMATCH_NOISE_AWARE_SCORERS", "1")
    mks = build_matchkeys([_short_code_profile()])
    assert _scorer_for(mks, "sku") == "qgram"


def test_bench_script_importable_and_pure_helpers():
    """The benchmark script imports (recordlinkage is lazy) and its scorer-env
    helper is correct without needing datasets."""
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parent.parent / "scripts" / "bench_noise_aware_scorer.py"
    spec = importlib.util.spec_from_file_location("bench_noise_aware_scorer", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.set_scorer("token_sort") == {"GOLDENMATCH_NOISE_AWARE_SCORERS": "0"}
    assert mod.set_scorer("ensemble") == {
        "GOLDENMATCH_NOISE_AWARE_SCORERS": "1",
        "GOLDENMATCH_NOISE_AWARE_TARGET": "ensemble",
    }


def _load_bench():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parent.parent / "scripts" / "bench_noise_aware_scorer.py"
    spec = importlib.util.spec_from_file_location("bench_noise_aware_scorer", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import pathlib as _pl
_NCVR = _pl.Path(__file__).parent / "benchmarks" / "datasets" / "NCVR" / "ncvoter_sample_10k.txt"


@pytest.mark.skipif(not _NCVR.exists(), reason="NCVR sample dataset missing")
def test_ncvr_high_jaro_winkler_meets_target(monkeypatch):
    """Pin the jaro_winkler NCVR-high F1 the default-on flip was based on
    (measured 0.9833; target 0.975 leaves headroom). Disable controller memory
    so the config is rebuilt under the scorer env."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    bench = _load_bench()
    res = bench.run_ncvr_high(bench.set_scorer("jaro_winkler"))
    assert res.get("f1", 0.0) >= 0.975, res
