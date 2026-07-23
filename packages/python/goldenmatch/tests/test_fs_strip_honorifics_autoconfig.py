"""FS honorific-stripping auto-config heuristic (GOLDENMATCH_FS_STRIP_HONORIFICS).

Default-OFF lever: appends the ``strip_honorifics`` transform to name-typed
comparison fields so a title/rank token leaked into a name field ("Sir",
"Baronet") stops carrying match weight. Targets the over-merge regime that TF
down-weighting could NOT reach on historical_50k (name coltype already routes
through name_freq_weighted_jw, so TF self-neutralizes; honorifics are the
residual). Spike A/B: F1 0.7520 -> 0.7628 (+0.0108).

OFF is byte-identical (no name field gets the transform). See
``_fs_strip_honorifics_enabled`` / ``_strip_honorifics_for`` /
``_STRIP_HONORIFIC_COLTYPES``.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.autoconfig import (
    _STRIP_HONORIFIC_COLTYPES,
    ColumnProfile,
    _strip_honorifics_for,
    build_probabilistic_matchkeys,
)

ON = "GOLDENMATCH_FS_STRIP_HONORIFICS"


def _p(name, col_type, card=0.3, null=0.0):
    return ColumnProfile(
        name=name, dtype="Utf8", col_type=col_type, confidence=0.9,
        null_rate=null, cardinality_ratio=card, avg_len=8,
    )


def _transforms(profiles):
    """{field: transforms-list} across all built probabilistic matchkeys."""
    mks = build_probabilistic_matchkeys(profiles)
    return {mf.field: list(mf.transforms) for mk in mks for mf in mk.fields}


def _has_strip(profiles):
    return {f: ("strip_honorifics" in t) for f, t in _transforms(profiles).items()}


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv(ON, raising=False)


# --- default ON: name fields get the transform (flipped 2026-07-23) ---------

def test_default_on_appends_strip_on_name_fields():
    # Flag unset -> default ON -> name/multi_name fields get strip_honorifics.
    strip = _has_strip([_p("first_name", "name"), _p("surname", "name"),
                        _p("occupation", "string"), _p("city", "geo")])
    assert strip == {"first_name": True, "surname": True,
                     "occupation": False, "city": False}


def test_explicit_off_no_field_gets_strip(monkeypatch):
    monkeypatch.setenv(ON, "0")
    profiles = [_p("first_name", "name"), _p("surname", "name")]
    assert all(v is False for v in _has_strip(profiles).values())


@pytest.mark.parametrize("falsy", ["0", "false", "off", "no", "disabled", "FALSE"])
def test_off_accepts_falsy_spellings(monkeypatch, falsy):
    monkeypatch.setenv(ON, falsy)
    assert _has_strip([_p("surname", "name")]) == {"surname": False}


def test_off_name_transforms_are_legacy_baseline(monkeypatch):
    # Explicit OFF must leave the name field's transform list at the legacy set.
    monkeypatch.setenv(ON, "0")
    t = _transforms([_p("surname", "name")])
    assert "strip_honorifics" not in t["surname"]


# --- ON: name-typed fields get the transform -------------------------------

def test_on_strip_is_last_transform():
    # Default ON; must run after lowercase/strip (append order matters).
    t = _transforms([_p("surname", "name")])["surname"]
    assert t[-1] == "strip_honorifics"
    assert "strip_honorifics" not in t[:-1]  # appended exactly once


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled", "TRUE"])
def test_on_accepts_truthy_spellings(monkeypatch, truthy):
    monkeypatch.setenv(ON, truthy)
    assert _has_strip([_p("surname", "name")]) == {"surname": True}


# --- ON: non-name col_types stay untouched ---------------------------------

def test_on_skips_non_name_types(monkeypatch):
    monkeypatch.setenv(ON, "1")
    strip = _has_strip([_p("occupation", "string"), _p("city", "geo"),
                        _p("postcode", "zip"), _p("email", "email", card=0.99)])
    assert all(v is False for v in strip.values())


# --- unit: _strip_honorifics_for boundary ----------------------------------

def test_strip_honorifics_for_helper(monkeypatch):
    monkeypatch.setenv(ON, "1")
    assert _strip_honorifics_for(_p("x", "name")) is True
    assert _strip_honorifics_for(_p("x", "multi_name")) is True
    assert _strip_honorifics_for(_p("x", "string")) is False
    assert _strip_honorifics_for(_p("x", "email")) is False


def test_strip_honorifics_for_on_by_default():
    # Flag unset -> default ON -> name types earn it, non-name types don't.
    assert _strip_honorifics_for(_p("x", "name")) is True
    assert _strip_honorifics_for(_p("x", "string")) is False


def test_strip_honorifics_for_explicit_off(monkeypatch):
    monkeypatch.setenv(ON, "0")
    assert _strip_honorifics_for(_p("x", "name")) is False


def test_eligible_coltypes_are_name_types():
    assert _STRIP_HONORIFIC_COLTYPES == frozenset({"name", "multi_name"})
    for bad in ("string", "geo", "zip", "email", "phone", "date", "numeric"):
        assert bad not in _STRIP_HONORIFIC_COLTYPES
