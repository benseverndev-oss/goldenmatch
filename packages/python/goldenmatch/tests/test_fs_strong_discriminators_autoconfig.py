"""FS discriminator negative-evidence auto-config (GOLDENMATCH_FS_STRONG_DISCRIMINATORS).

Default-OFF lever: adds a one-sided NE penalty on discriminator comparison fields
(date/geo/zip/high-card-string, e.g. dob/birth_place) so a CONFIDENT disagreement
subtracts penalty_bits. Targets the namesake over-merge (false merges agree on
names but disagree on birth_place/dob). NE never boosts on agreement, so true
matches (which agree on the discriminator) are untouched — no recall cost.

OFF is byte-identical (negative_evidence stays None). See
``_fs_strong_discriminators_enabled`` / ``_discriminator_ne_fields``.
"""
from __future__ import annotations

import pytest

from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _discriminator_ne_fields,
    _fs_strong_discriminators_enabled,
    build_probabilistic_matchkeys,
)

ON = "GOLDENMATCH_FS_STRONG_DISCRIMINATORS"
BITS = "GOLDENMATCH_FS_DISCRIMINATOR_PENALTY_BITS"


def _p(name, col_type, card=0.3, null=0.0):
    return ColumnProfile(
        name=name, dtype="Utf8", col_type=col_type, confidence=0.9,
        null_rate=null, cardinality_ratio=card, avg_len=8,
    )


def _ne(profiles):
    mks = build_probabilistic_matchkeys(profiles)
    ne = mks[0].negative_evidence if mks else None
    return {n.field: n for n in (ne or [])}


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv(ON, raising=False)
    monkeypatch.delenv(BITS, raising=False)


# historical_50k-shaped profiles.
def _profiles():
    return [
        _p("first_name", "name"), _p("surname", "name"),
        _p("occupation", "string", card=0.05),      # low-card -> weak, excluded
        _p("birth_place", "string", card=0.4),       # high-card -> discriminator
        _p("dob", "date"), _p("postcode_fake", "zip", card=0.6),
    ]


def test_default_off_no_ne():
    assert _fs_strong_discriminators_enabled() is False
    mks = build_probabilistic_matchkeys(_profiles())
    assert mks[0].negative_evidence is None


def test_explicit_off_no_ne(monkeypatch):
    monkeypatch.setenv(ON, "0")
    assert build_probabilistic_matchkeys(_profiles())[0].negative_evidence is None


def test_on_adds_discriminator_ne(monkeypatch):
    monkeypatch.setenv(ON, "1")
    ne = _ne(_profiles())
    assert "dob" in ne and "birth_place" in ne and "postcode_fake" in ne


def test_on_excludes_names_and_weak_categoricals(monkeypatch):
    monkeypatch.setenv(ON, "1")
    ne = _ne(_profiles())
    for excluded in ("first_name", "surname", "occupation"):
        assert excluded not in ne


def test_ne_scorer_and_threshold(monkeypatch):
    monkeypatch.setenv(ON, "1")
    ne = _ne(_profiles())
    assert ne["dob"].scorer == "exact" and ne["dob"].threshold == pytest.approx(0.99)
    assert ne["birth_place"].scorer == "jaro_winkler"
    assert ne["birth_place"].threshold == pytest.approx(0.75)


def test_ne_is_one_sided_penalty_bits(monkeypatch):
    # penalty_bits set -> one-sided, EM-independent, decoupled from name evidence.
    monkeypatch.setenv(ON, "1")
    ne = _ne(_profiles())
    assert all(n.penalty_bits == pytest.approx(3.0) for n in ne.values())
    assert all(n.penalty is None for n in ne.values())  # not the weighted-style flat penalty


def test_penalty_bits_env_override(monkeypatch):
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv(BITS, "5.5")
    ne = _ne(_profiles())
    assert ne["dob"].penalty_bits == pytest.approx(5.5)


def test_discriminator_still_positive_field(monkeypatch):
    # The discriminator must ALSO remain a positive comparison field (keeps the
    # agreement boost for true matches); NE only stacks on disagreement.
    monkeypatch.setenv(ON, "1")
    mks = build_probabilistic_matchkeys(_profiles())
    field_names = {f.field for f in mks[0].fields}
    assert {"dob", "birth_place"} <= field_names


def test_helper_returns_none_when_off():
    fields = build_probabilistic_matchkeys(_profiles())[0].fields
    assert _discriminator_ne_fields(_profiles(), fields) is None
