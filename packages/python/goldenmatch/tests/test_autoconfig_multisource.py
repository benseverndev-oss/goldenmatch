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


# ── Task 5: _source_correlated_exclusions ────────────────────────────────────

from goldenmatch.core.autoconfig import _source_correlated_exclusions


def test_exclusions_dunder_source():
    df = pl.DataFrame({
        "__source__": ["a", "a", "b", "b"],
        "surrogate": ["s1", "s2", "s3", "s4"],   # 0-overlap -> excluded
        "email": ["x@y", "p@q", "x@y", "z@w"],   # shared -> kept
        "name": ["A", "B", "A", "C"],
    })
    profiles = _profiles(df)
    part = _detect_source_partition(df, profiles)
    excl = _source_correlated_exclusions(df, profiles, part)
    assert "surrogate" in excl
    assert "email" not in excl
    assert "name" not in excl
    assert "__source__" not in excl   # handled by the dunder skip, not added here


def test_exclusions_user_source_includes_the_source_column():
    df = pl.DataFrame({
        "source": ["hubspot", "hubspot", "salesforce", "salesforce"],
        "crm_id": ["h1", "h2", "s1", "s2"],
        "email": ["x@y", "p@q", "x@y", "z@w"],
    })
    profiles = _profiles(df)
    part = _detect_source_partition(df, profiles)   # == "source"
    excl = _source_correlated_exclusions(df, profiles, part)
    assert "source" in excl       # the partition label itself
    assert "crm_id" in excl       # 0-overlap surrogate
    assert "email" not in excl


def test_exclusions_empty_when_no_partition():
    df = pl.DataFrame({"email": ["x@y"], "name": ["A"]})
    assert _source_correlated_exclusions(df, _profiles(df), None) == set()


# ── Task 6: phone demotion in build_matchkeys ────────────────────────────────

from goldenmatch.core.autoconfig import build_matchkeys


def _phone_df():
    # 10 distinct 10-digit phones, each repeated -> cardinality 0.5 (a real
    # exact candidate: not < 0.5, not perfectly-unique >= 1.0).
    phones = [f"5551{i:06d}" for i in range(10)]
    return pl.DataFrame({
        "phone": phones + phones,
        "name": [f"person {i}" for i in range(20)],
    })


def test_phone_is_exact_matchkey_single_source():
    df = _phone_df()
    mks = build_matchkeys(_profiles(df), df=df, multi_source=False)
    assert any(m.name == "exact_phone" for m in mks)


def test_phone_demoted_when_multi_source():
    df = _phone_df()
    mks = build_matchkeys(_profiles(df), df=df, multi_source=True)
    assert not any(m.name == "exact_phone" for m in mks)
    # phone is not silently turned into a weighted field either
    for m in mks:
        assert all(f.field != "phone" for f in (m.fields or []))


# ── Task 7: wired into auto_configure_df ─────────────────────────────────────

from goldenmatch.core.autoconfig import auto_configure_df


def _crm_df():
    rows = []
    srcs = ["hubspot", "salesforce", "cvent"]
    for i in range(30):
        s = srcs[i % 3]
        rows.append({
            "source": s,
            "rec_id": f"{s}-{i}",                  # disjoint per source
            "first": f"first{i // 2}",
            "last": f"last{i // 2}",
            "email": f"user{i // 2}@ex.com",       # shared across sources
            "phone": "5551112222" if i < 6 else f"555{i:07d}",
        })
    return pl.DataFrame(rows)


def _mk_names(cfg):
    return {m.name for m in cfg.get_matchkeys()}


def _all_match_fields(cfg):
    return {
        f.field
        for m in cfg.get_matchkeys()
        for f in (m.fields or [])
        if f.field
    }


def test_zeroconfig_excludes_source_and_demotes_phone():
    cfg = auto_configure_df(_crm_df())
    fields = _all_match_fields(cfg)
    assert "source" not in fields          # source label excluded
    assert "rec_id" not in fields          # 0-overlap surrogate excluded
    assert "exact_phone" not in _mk_names(cfg)   # phone demoted
    assert "email" in fields               # genuine shared identifier kept


def test_killswitch_restores_legacy(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_MULTISOURCE_AUTOCONFIG", "0")
    cfg = auto_configure_df(_crm_df())
    fields = _all_match_fields(cfg)
    # legacy behaviour: source admitted OR phone exact present
    assert "source" in fields or "exact_phone" in _mk_names(cfg)


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
