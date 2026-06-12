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


# ── Task 4: _detect_source_partition ─────────────────────────────────────────

from goldenmatch.core.autoconfig import _detect_source_partition, profile_columns


def _profiles(df):
    return profile_columns(df)   # returns list[ColumnProfile] directly


def test_detect_dunder_source():
    df = pl.DataFrame({"__source__": ["a", "a", "b"], "rid": ["1", "2", "3"]})
    assert _detect_source_partition(df, _profiles(df)) == "__source__"


def test_detect_none_single_source():
    df = pl.DataFrame({"__source__": ["a", "a"], "rid": ["1", "2"]})  # 1 distinct
    assert _detect_source_partition(df, _profiles(df)) is None


def test_detect_user_source_column_with_cosignature():
    df = pl.DataFrame({
        "source": ["hubspot", "hubspot", "salesforce", "salesforce"],
        "crm_id": ["h1", "h2", "s1", "s2"],   # disjoint per source -> co-signature
        "name": ["a", "b", "c", "d"],
    })
    assert _detect_source_partition(df, _profiles(df)) == "source"


def test_detect_none_user_source_without_cosignature():
    df = pl.DataFrame({
        "channel": ["web", "web", "phone", "phone"],
        "email": ["x@y", "x@y", "x@y", "x@y"],  # fully shared -> no co-signature
    })
    assert _detect_source_partition(df, _profiles(df)) is None


def test_detect_suppressed_in_match_mode():
    df = pl.DataFrame({"__source__": ["a", "b"], "rid": ["1", "2"]})
    with ac._match_mode_autoconfig():
        assert _detect_source_partition(df, _profiles(df)) is None


def test_detect_suppressed_by_killswitch(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_MULTISOURCE_AUTOCONFIG", "0")
    df = pl.DataFrame({"__source__": ["a", "b"], "rid": ["1", "2"]})
    assert _detect_source_partition(df, _profiles(df)) is None


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
