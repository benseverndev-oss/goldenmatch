"""Tests for guarded / conditional matchkeys.

Spec: docs/superpowers/specs/2026-07-26-guarded-matchkeys-design.md

A guard is a pair predicate (over ``a_<col>``/``b_<col>``) that gates whether a
matchkey fires for a candidate pair. v1: matchkey-level guards on exact +
weighted matchkeys, evaluated against RAW (pre-prep) values.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.guard import (
    GuardError,
    guard_columns,
    guard_passes,
    validate_guard,
)

_SSN_GUARD = "a_ssn != '000-00-0000' and b_ssn != '000-00-0000'"


# ── guard helper ─────────────────────────────────────────────────────────────


def test_guard_columns_strips_prefixes():
    assert guard_columns(_SSN_GUARD) == {"ssn"}
    assert guard_columns("a_x == b_y or a_z in ('p', 'q')") == {"x", "y", "z"}


def test_guard_columns_rejects_bare_name():
    with pytest.raises(GuardError):
        guard_columns("ssn == 1")


def test_guard_columns_rejects_bad_expression():
    with pytest.raises(GuardError):
        guard_columns("a_x +")  # syntax error


def test_guard_passes_both_sides():
    cols = {"ssn"}
    assert guard_passes(_SSN_GUARD, cols, {"ssn": "123"}, {"ssn": "456"}) is True
    assert guard_passes(_SSN_GUARD, cols, {"ssn": "000-00-0000"}, {"ssn": "456"}) is False
    assert guard_passes(_SSN_GUARD, cols, {"ssn": "123"}, {"ssn": "000-00-0000"}) is False


def test_guard_passes_missing_value_is_no_fire():
    # An absent value -> the reference misses -> the clause does not fire.
    assert guard_passes(_SSN_GUARD, {"ssn"}, {}, {"ssn": "456"}) is False


def test_validate_guard_unknown_column():
    validate_guard(_SSN_GUARD, {"ssn", "name"})
    with pytest.raises(GuardError):
        validate_guard(_SSN_GUARD, {"name"})


# ── schema validation ────────────────────────────────────────────────────────


def test_matchkey_guard_accepted_on_exact_and_weighted():
    MatchkeyConfig(name="e", type="exact", fields=[MatchkeyField(field="ssn")], guard=_SSN_GUARD)
    MatchkeyConfig(
        name="w", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        guard="a_country == b_country",
    )


def test_probabilistic_matchkey_guard_deferred():
    with pytest.raises(ValueError, match="planned follow-up"):
        MatchkeyConfig(
            name="p", type="probabilistic",
            fields=[MatchkeyField(field="x", scorer="exact")], guard="a_x == b_x",
        )


def test_field_level_guard_deferred():
    with pytest.raises(ValueError, match="planned follow-up"):
        MatchkeyConfig(
            name="w", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0,
                                  guard="a_country == b_country")],
        )


def test_matchkey_guard_bare_name_rejected():
    with pytest.raises(ValueError, match="prefixed"):
        MatchkeyConfig(name="e", type="exact", fields=[MatchkeyField(field="ssn")], guard="ssn == 1")


# ── exact matchkey-level guard (end to end) ──────────────────────────────────


def _run(df, cfg):
    res = gm.dedupe_df(df, config=cfg)
    return [sorted(c["members"]) for c in res.clusters.values() if c["size"] > 1]


def test_exact_guard_suppresses_placeholder_merge():
    # rows 0,1 share the placeholder SSN (must NOT merge); 2,3 share a real SSN.
    df = pl.DataFrame({
        "id": [0, 1, 2, 3],
        "ssn": ["000-00-0000", "000-00-0000", "111-22-3333", "111-22-3333"],
        "name": ["Alice", "Bob", "Carol", "Dave"],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="ssn", type="exact",
                                  fields=[MatchkeyField(field="ssn")], guard=_SSN_GUARD)],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["ssn"])]),
    )
    multi = _run(df, cfg)
    assert [0, 1] not in multi   # placeholder pair suppressed
    assert [2, 3] in multi       # real-SSN pair merged


def test_exact_guard_is_per_matchkey_not_global_veto():
    # rows 0,1 share the placeholder SSN AND the same name -> still merge via name.
    df = pl.DataFrame({
        "id": [0, 1, 2, 3],
        "ssn": ["000-00-0000", "000-00-0000", "111-22-3333", "111-22-3333"],
        "name": ["Sam", "Sam", "Carol", "Dave"],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="ssn", type="exact", fields=[MatchkeyField(field="ssn")], guard=_SSN_GUARD),
            MatchkeyConfig(name="name", type="exact", fields=[MatchkeyField(field="name")]),
        ],
        blocking=BlockingConfig(strategy="multi_pass",
                                keys=[BlockingKeyConfig(fields=["ssn"]), BlockingKeyConfig(fields=["name"])]),
    )
    multi = _run(df, cfg)
    assert [0, 1] in multi   # merged via the unguarded name matchkey
    assert [2, 3] in multi   # merged via the guarded ssn matchkey (real value)


def test_exact_guard_uses_raw_values_not_standardized():
    # The SSN column is auto-standardized to phone form during prep; the guard
    # must still see the raw '000-00-0000' the config author wrote against.
    df = pl.DataFrame({
        "id": [0, 1],
        "ssn": ["000-00-0000", "000-00-0000"],
        "name": ["A", "B"],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="ssn", type="exact",
                                  fields=[MatchkeyField(field="ssn")], guard=_SSN_GUARD)],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["ssn"])]),
    )
    assert _run(df, cfg) == []   # guard sees raw placeholder -> no merge


def test_no_guard_config_unaffected():
    # A guard that is always true must produce the same clusters as no guard.
    df = pl.DataFrame({
        "id": [0, 1, 2],
        "ssn": ["111-22-3333", "111-22-3333", "999-88-7777"],
        "name": ["A", "B", "C"],
    })
    base = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="ssn", type="exact", fields=[MatchkeyField(field="ssn")])],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["ssn"])]),
    )
    guarded = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="ssn", type="exact", fields=[MatchkeyField(field="ssn")],
                                  guard="a_ssn != '' and b_ssn != ''")],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["ssn"])]),
    )
    assert _run(df, base) == _run(df, guarded) == [[0, 1]]


# ── weighted matchkey-level guard (end to end) ───────────────────────────────


def test_weighted_guard_suppresses_cross_country_match():
    # Same/similar names across a country boundary must not merge when guarded.
    df = pl.DataFrame({
        "id": [0, 1, 2],
        "name": ["John Smith", "John Smith", "John Smith"],
        "country": ["US", "US", "CA"],
        "zip": ["1", "1", "1"],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="nm", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            guard="a_country == b_country",
        )],
        blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])]),
    )
    multi = _run(df, cfg)
    assert [0, 1] in multi          # same country -> merged
    assert all(2 not in c for c in multi)   # CA row never merged across the border
