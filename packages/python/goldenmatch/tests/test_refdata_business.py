"""Tests for goldenmatch.refdata.business + legal_form_strip transform."""

from __future__ import annotations

import goldenmatch.refdata  # noqa: F401  registers the transform
import pytest
from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata import (
    business_available,
    legal_form_variants,
    strip_legal_form,
)

# ── data load ───────────────────────────────────────────────────────────────


def test_data_is_bundled():
    assert business_available() is True


def test_variant_count_reasonable():
    """Bundled list should cover the major forms across jurisdictions."""
    assert len(legal_form_variants()) >= 60


# ── strip behavior ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("Acme Inc.", "Acme"),
    ("Acme Inc", "Acme"),
    ("Acme, Inc.", "Acme"),
    ("Acme Incorporated", "Acme"),
    ("Acme Corp.", "Acme"),
    ("Acme Corp", "Acme"),
    ("Acme Corporation", "Acme"),
    ("Acme LLC", "Acme"),
    ("Acme L.L.C.", "Acme"),
    ("Acme Limited Liability Company", "Acme"),
    ("Acme Co.", "Acme"),
    ("Acme Co", "Acme"),
    ("Acme Company", "Acme"),
    ("Acme Ltd.", "Acme"),
    ("Acme Limited", "Acme"),
    ("Acme PLC", "Acme"),
    ("Acme LLP", "Acme"),
    ("Acme GmbH", "Acme"),
    ("Acme AG", "Acme"),
    ("Acme SA", "Acme"),
    ("Acme S.A.", "Acme"),
    ("Acme SARL", "Acme"),
    ("Acme BV", "Acme"),
    ("Acme NV", "Acme"),
    ("Acme SpA", "Acme"),
    ("Acme Pty Ltd", "Acme"),
    ("Acme Pty. Ltd.", "Acme"),
    ("Acme Pvt Ltd", "Acme"),
])
def test_strip_known_suffixes(raw: str, expected: str):
    assert strip_legal_form(raw) == expected


def test_case_insensitive():
    assert strip_legal_form("ACME INC.") == "ACME"
    assert strip_legal_form("acme llc") == "acme"
    assert strip_legal_form("Acme inc") == "Acme"


def test_normalizes_whitespace():
    assert strip_legal_form("   Acme   Co.   ") == "Acme"
    assert strip_legal_form("Acme  Pty   Ltd") == "Acme"


def test_iterative_strip_multiple_suffixes():
    """A name like 'Acme Holdings Inc' should strip both Inc and Holdings."""
    assert strip_legal_form("Acme Holdings Inc") == "Acme"
    assert strip_legal_form("Acme Industries Corp.") == "Acme"


def test_does_not_strip_mid_name():
    """The suffix must be trailing — 'Inc' in the middle stays."""
    assert strip_legal_form("Inc Tower") == "Inc Tower"
    assert strip_legal_form("Plain Company Name") == "Plain Company Name"


def test_idempotent():
    cleaned = strip_legal_form("Acme Inc.")
    assert strip_legal_form(cleaned) == cleaned


def test_no_match_returns_input():
    assert strip_legal_form("Just A Name") == "Just A Name"


def test_none_returns_none():
    assert strip_legal_form(None) is None


def test_empty_returns_empty():
    assert strip_legal_form("") == ""
    assert strip_legal_form("   ") == ""


def test_strip_does_not_consume_entire_name():
    """If the whole input is a legal form, returning '' would be wrong —
    we keep the input rather than nuke it."""
    # The bare suffix as a "name" is degenerate; behavior here is best-
    # effort. Verify we don't crash and don't produce empty for "Inc."
    # alone (the regex won't match because there's no preceding word
    # boundary).
    assert strip_legal_form("Inc.") == "Inc."


# ── integration ─────────────────────────────────────────────────────────────


def test_apply_transform_dispatches_to_plugin():
    from goldenmatch.utils.transforms import apply_transform

    assert apply_transform("Acme Inc.", "legal_form_strip") == "Acme"
    assert apply_transform(None, "legal_form_strip") is None


def test_apply_transforms_chain():
    """The transform should compose with other transforms."""
    from goldenmatch.utils.transforms import apply_transforms

    # Strip legal form, then lowercase.
    assert apply_transforms("Acme Inc.", ["legal_form_strip", "lowercase"]) == "acme"


def test_transform_registered():
    assert PluginRegistry.instance().has_transform("legal_form_strip")


def test_field_transform_validator_accepts_plugin():
    from goldenmatch.config.schemas import FieldTransform

    # Should not raise.
    FieldTransform(transform="legal_form_strip")


def test_field_transform_validator_rejects_unknown():
    from goldenmatch.config.schemas import FieldTransform

    with pytest.raises(ValueError, match="Invalid transform"):
        FieldTransform(transform="totally_made_up_transform")


def test_matchkey_field_accepts_transform():
    """End-to-end: a MatchkeyField with legal_form_strip in transforms validates."""
    from goldenmatch.config.schemas import MatchkeyField

    field = MatchkeyField(
        field="company_name",
        scorer="jaro_winkler",
        weight=1.0,
        transforms=["legal_form_strip", "lowercase"],
    )
    assert field.transforms == ["legal_form_strip", "lowercase"]
