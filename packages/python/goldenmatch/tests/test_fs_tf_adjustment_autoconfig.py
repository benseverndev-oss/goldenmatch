"""FS term-frequency adjustment auto-config heuristic (GOLDENMATCH_FS_TF_ADJUSTMENT).

Default-OFF lever: sets ``MatchkeyField.tf_adjustment=True`` on skewed-value
discrete-categorical comparison fields so an exact agreement on a RARE value
out-weights one on a common value. The Winkler machinery already exists in
``probabilistic.py``; this is the seam that makes zero-config FS actually use it.
Targets the over-merge regime (historical_50k precision 0.72 vs Splink 0.97).

OFF is byte-identical (no field gets the flag). See ``_fs_tf_adjustment_enabled``
/ ``_tf_adjustment_for`` / ``_TF_ELIGIBLE_COLTYPES``.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.autoconfig import (
    _TF_CARD_CEILING,
    _TF_ELIGIBLE_COLTYPES,
    ColumnProfile,
    _tf_adjustment_for,
    build_probabilistic_matchkeys,
)

ON = "GOLDENMATCH_FS_TF_ADJUSTMENT"


def _p(name, col_type, card=0.3, null=0.0):
    return ColumnProfile(
        name=name, dtype="Utf8", col_type=col_type, confidence=0.9,
        null_rate=null, cardinality_ratio=card, avg_len=8,
    )


def _tf_fields(profiles):
    mks = build_probabilistic_matchkeys(profiles)
    return {mf.field: mf.tf_adjustment for mk in mks for mf in mk.fields}


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv(ON, raising=False)


# --- default OFF: byte-identical (no field carries the flag) ---------------

def test_default_off_no_field_gets_tf():
    profiles = [_p("surname", "name"), _p("occupation", "string"),
                _p("city", "geo"), _p("postcode", "zip")]
    assert all(v is False for v in _tf_fields(profiles).values())


def test_explicit_off_no_field_gets_tf(monkeypatch):
    monkeypatch.setenv(ON, "0")
    profiles = [_p("surname", "name"), _p("occupation", "string")]
    assert all(v is False for v in _tf_fields(profiles).values())


# --- ON: skewed categoricals get the flag ---------------------------------

def test_on_sets_tf_on_eligible_categoricals(monkeypatch):
    monkeypatch.setenv(ON, "1")
    profiles = [_p("surname", "name"), _p("occupation", "string"),
                _p("city", "geo"), _p("postcode", "zip")]
    tf = _tf_fields(profiles)
    assert tf == {"surname": True, "occupation": True,
                  "city": True, "postcode": True}


@pytest.mark.parametrize("truthy", ["1", "true", "on", "yes", "enabled", "TRUE"])
def test_on_accepts_truthy_spellings(monkeypatch, truthy):
    monkeypatch.setenv(ON, truthy)
    assert _tf_fields([_p("occupation", "string")]) == {"occupation": True}


# --- ON: ineligible col_types stay off ------------------------------------

def test_on_skips_identity_value_types(monkeypatch):
    # email/phone are near-unique identity values -> TF inert -> not flagged.
    monkeypatch.setenv(ON, "1")
    tf = _tf_fields([_p("email", "email", card=0.99),
                     _p("phone", "phone", card=0.99)])
    assert all(v is False for v in tf.values())


def test_on_skips_near_unique_categorical(monkeypatch):
    # A categorical whose values are near-unique: TF bump ~0, skipped to save
    # the frequency-table build (behavior-neutral either way).
    monkeypatch.setenv(ON, "1")
    tf = _tf_fields([_p("occupation", "string", card=_TF_CARD_CEILING + 0.05)])
    assert tf.get("occupation") is False


def test_on_skips_below_card_floor(monkeypatch):
    # A near-constant categorical (below the fuzzy card floor) is dropped from
    # the comparison set entirely (v2 lever 2b) OR, if kept, not TF-flagged.
    monkeypatch.setenv(ON, "1")
    tf = _tf_fields([_p("gender", "string", card=0.002)])
    assert tf.get("gender", False) is False


# --- unit: _tf_adjustment_for boundary ------------------------------------

def test_tf_adjustment_for_helper(monkeypatch):
    monkeypatch.setenv(ON, "1")
    assert _tf_adjustment_for(_p("x", "string", card=0.3)) is True
    assert _tf_adjustment_for(_p("x", "identifier", card=0.3)) is False
    assert _tf_adjustment_for(_p("x", "string", card=1.0)) is False


def test_eligible_coltypes_are_categoricals():
    # Guard the intended set: skewed discrete categoricals only, no magnitude
    # (date/numeric) or near-unique identity (email/phone/identifier) types.
    assert _TF_ELIGIBLE_COLTYPES == frozenset({"name", "string", "geo", "zip"})
    for bad in ("email", "phone", "identifier", "date", "numeric", "description"):
        assert bad not in _TF_ELIGIBLE_COLTYPES
