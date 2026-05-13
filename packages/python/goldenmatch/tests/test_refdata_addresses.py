"""Tests for goldenmatch.refdata.addresses + address_normalize transform."""

from __future__ import annotations

import goldenmatch.refdata  # noqa: F401  registers transform
import pytest
from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata import (
    address_tokens,
    addresses_available,
    normalize_address,
)

# ── data load ───────────────────────────────────────────────────────────────


def test_data_is_bundled():
    assert addresses_available() is True


def test_token_count_reasonable():
    """Bundled list should cover the major USPS abbreviations."""
    assert len(address_tokens()) >= 300


# ── normalization ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("123 Main Street", "123 main st"),
    ("123 Main St", "123 main st"),
    ("123 Main St.", "123 main st"),
    ("123 Main Str", "123 main st"),
    ("456 Park Avenue", "456 park ave"),
    ("456 Park Av", "456 park ave"),
    ("789 Oak Boulevard", "789 oak blvd"),
    ("789 Oak Blvd", "789 oak blvd"),
    # 'Lake' is itself a USPS suffix variant -> 'lk'. The transform doesn't
    # do positional reasoning, so it normalizes every occurrence. Match
    # invariance still holds: 'Lake Road' and 'Lk Rd' both collapse here.
    ("100 Lake Road", "100 lk rd"),
    ("100 Lake Rd", "100 lk rd"),
    # 'Court' likewise -> 'ct'. Same invariance argument.
    ("250 Court Drive", "250 ct dr"),
    ("400 Highway Avenue", "400 hwy ave"),
    ("400 Hwy Ave", "400 hwy ave"),
    ("12 Circle", "12 cir"),
    ("12 Cir", "12 cir"),
    ("3 Lane", "3 ln"),
    ("3 Ln", "3 ln"),
])
def test_street_suffix_canonicalization(raw: str, expected: str):
    assert normalize_address(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("100 Main St North", "100 main st n"),
    ("100 Main St N", "100 main st n"),
    ("100 Main St South", "100 main st s"),
    ("100 Main St S", "100 main st s"),
    ("100 Main St Northeast", "100 main st ne"),
    ("100 Main St NE", "100 main st ne"),
    ("100 Main St Southwest", "100 main st sw"),
    ("100 Main St SW", "100 main st sw"),
])
def test_directional_canonicalization(raw: str, expected: str):
    assert normalize_address(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("789 Oak St Apartment 5", "789 oak st apt 5"),
    ("789 Oak St Apt 5", "789 oak st apt 5"),
    ("789 Oak St Suite 200", "789 oak st ste 200"),
    ("789 Oak St Ste 200", "789 oak st ste 200"),
    ("789 Oak St Floor 3", "789 oak st fl 3"),
    ("789 Oak St Fl 3", "789 oak st fl 3"),
    ("789 Oak St Building A", "789 oak st bldg a"),
])
def test_secondary_unit_canonicalization(raw: str, expected: str):
    assert normalize_address(raw) == expected


def test_multi_token_combined():
    """Compound case: directional + suffix + secondary unit all collapse."""
    raw_long = "100 First Avenue Northeast Suite 200"
    raw_short = "100 First Ave NE Ste 200"
    assert normalize_address(raw_long) == normalize_address(raw_short)
    assert normalize_address(raw_long) == "100 first ave ne ste 200"


def test_case_insensitive():
    assert normalize_address("123 MAIN STREET") == "123 main st"
    assert normalize_address("123 main street") == "123 main st"
    assert normalize_address("123 Main Street") == "123 main st"


def test_punctuation_stripped():
    assert normalize_address("789 Oak Blvd., Apt. 5") == "789 oak blvd apt 5"
    assert normalize_address("789, Oak St.") == "789 oak st"


def test_unknown_tokens_pass_through():
    """Words that aren't in the table stay unchanged (just lower-cased)."""
    assert normalize_address("123 Mockingbird Lane") == "123 mockingbird ln"
    assert normalize_address("PO Box 42") == "po box 42"  # 'box' isn't a suffix


def test_aggressive_normalization_preserves_match_invariance():
    """The transform is position-agnostic: it normalizes every USPS-known
    token, not just trailing ones. That's fine for matching as long as
    BOTH sides reduce to the same canonical form. This test pins that
    invariance for cases where the lookup is over-eager (e.g. 'Lake' is
    both a name part and a USPS suffix variant)."""
    pairs = [
        ("100 Lake Road", "100 Lk Rd"),
        ("250 Court Drive", "250 Ct Dr"),
        ("3 Park Avenue South", "3 Park Ave S"),
        ("42 Main Boulevard North", "42 Main Blvd N"),
    ]
    for a, b in pairs:
        assert normalize_address(a) == normalize_address(b), \
            f"{a!r} and {b!r} should reduce to the same canonical form"


def test_pound_apartment_invariant_with_apt():
    """Regression for PR #219 review: ``#5`` and ``Apt 5`` are
    semantically identical apartment designators but tokenize differently
    on whitespace/comma boundaries. Without a pre-tokenization rewrite,
    ``"123 Main St #5"`` and ``"123 Main St Apt 5"`` reduce to different
    canonicals — invariance broken. The fix preprocesses ``#<digits>``
    to ``apt <digits>`` before tokenization so both sides reduce to
    ``"123 main st apt 5"``."""
    assert normalize_address("123 Main St #5") == normalize_address("123 Main St Apt 5")
    assert normalize_address("123 Main St #5") == "123 main st apt 5"
    # ``#`` with whitespace before the digits.
    assert normalize_address("789 Oak Blvd # 12") == "789 oak blvd apt 12"
    # Non-numeric `#tag` is left alone (no apt rewrite). The
    # _LEAD_PUNCT_STRIP_RE still drops the leading `#` per the existing
    # behavior — `#tag` becomes `tag`. Documenting current shape.
    out = normalize_address("123 Main St #tag")
    assert "apt" not in out


def test_po_box_variants_invariant():
    """Regression for PR #219 review: ``PO Box``, ``P.O. Box``,
    ``P. O. Box``, and ``POBOX`` are the same address designator but
    tokenize/canonicalize to different strings without preprocessing.
    The pre-tokenization rewrite collapses all variants to ``po box``."""
    canonical = normalize_address("PO Box 42")
    assert normalize_address("P.O. Box 42") == canonical
    assert normalize_address("P. O. Box 42") == canonical
    assert normalize_address("POBOX 42") == canonical
    assert canonical == "po box 42"


def test_idempotent():
    once = normalize_address("123 Main Street North")
    twice = normalize_address(once)
    assert once == twice


def test_whitespace_collapsed():
    assert normalize_address("  123   Main   Street  ") == "123 main st"


def test_none_returns_none():
    assert normalize_address(None) is None


def test_empty_returns_empty():
    assert normalize_address("") == ""
    assert normalize_address("   ") == ""


# ── integration ─────────────────────────────────────────────────────────────


def test_apply_transform_dispatches_to_plugin():
    from goldenmatch.utils.transforms import apply_transform

    assert apply_transform("123 Main Street", "address_normalize") == "123 main st"


def test_apply_transforms_chain():
    """The transform composes with others."""
    from goldenmatch.utils.transforms import apply_transforms

    # Address normalize, then strip_all (no whitespace) -- a 'fuzzy key'.
    out = apply_transforms("123 Main Street", ["address_normalize", "strip_all"])
    assert out == "123mainst"


def test_transform_registered():
    assert PluginRegistry.instance().has_transform("address_normalize")


def test_field_transform_validator_accepts_plugin():
    from goldenmatch.config.schemas import FieldTransform

    FieldTransform(transform="address_normalize")  # should not raise


def test_matchkey_field_accepts_transform():
    from goldenmatch.config.schemas import MatchkeyField

    field = MatchkeyField(
        field="address",
        scorer="jaro_winkler",
        weight=1.0,
        transforms=["address_normalize"],
    )
    assert field.transforms == ["address_normalize"]
