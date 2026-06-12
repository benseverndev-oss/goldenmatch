"""#858: zero-config multi-source over-merge guard.

Tests the source-partition detection, source-correlated exclusion, phone
demotion, and the dedupe-only / single-source / match-mode firewalls.
"""
from __future__ import annotations

import polars as pl

from goldenmatch.core import autoconfig as ac
from goldenmatch.core.autoconfig import _check_source_overlap


# ── Task 2: kill-switch ──────────────────────────────────────────────────────

def test_killswitch_default_on_and_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_MULTISOURCE_AUTOCONFIG", raising=False)
    assert ac._multisource_autoconfig_enabled() is True
    monkeypatch.setenv("GOLDENMATCH_MULTISOURCE_AUTOCONFIG", "0")
    assert ac._multisource_autoconfig_enabled() is False
    monkeypatch.setenv("GOLDENMATCH_MULTISOURCE_AUTOCONFIG", "false")
    assert ac._multisource_autoconfig_enabled() is False


# ── Task 3: match-mode ContextVar ────────────────────────────────────────────

def test_match_mode_contextvar_default_and_scoped():
    assert ac._AUTOCONFIG_MATCH_MODE.get() is False
    with ac._match_mode_autoconfig():
        assert ac._AUTOCONFIG_MATCH_MODE.get() is True
    assert ac._AUTOCONFIG_MATCH_MODE.get() is False


# ── Task 1: generalized _check_source_overlap ────────────────────────────────

def test_overlap_against_user_partition_column():
    df = pl.DataFrame({
        "src": ["a", "a", "b", "b"],
        "rid": ["1", "2", "3", "4"],            # disjoint across src -> 0.0
        "email": ["x@y.com", "p@q.com", "x@y.com", "z@w.com"],  # shares x@y.com
    })
    assert _check_source_overlap(df, "rid", partition_col="src") == 0.0
    assert _check_source_overlap(df, "email", partition_col="src") > 0.0


def test_overlap_default_partition_is_dunder_source():
    df = pl.DataFrame({"__source__": ["a", "b"], "rid": ["1", "2"]})
    assert _check_source_overlap(df, "rid") == 0.0          # disjoint
    # absent partition -> 1.0 (fail-open)
    assert _check_source_overlap(pl.DataFrame({"rid": ["1"]}), "rid") == 1.0
