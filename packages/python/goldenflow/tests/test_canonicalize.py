"""Tests for the pure scalar canonicalizers (#1128).

These pin the byte-level contract a JS/TS port must mirror: the four ``kind``
rules, plus the cross-cutting guarantees (total, idempotent, ASCII-only/
locale-independent, dependency-free).
"""

from __future__ import annotations

import pytest
from goldenflow import canonicalize

KINDS = ("email", "phone", "name", "postal")


# ── email ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice@Example.COM", "alice@example.com"),
        ("  bob@x.org  ", "bob@x.org"),
        ("\tCarol@Y.io\n", "carol@y.io"),
        ("already@clean.com", "already@clean.com"),
        ("", ""),
    ],
)
def test_email(raw, expected):
    assert canonicalize(raw, "email") == expected


# ── phone ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(555) 123-4567", "5551234567"),
        ("+1 555 123 4567", "5551234567"),       # NANP country code stripped
        ("1-555-123-4567", "5551234567"),
        ("15551234567", "5551234567"),            # bare 11-digit leading 1
        ("555.123.4567", "5551234567"),
        ("+44 20 7946 0958", "442079460958"),     # not 11 digits -> no strip
        ("12345678901", "2345678901"),            # 11 digits, leading 1 -> strip
        ("1234567890", "1234567890"),             # 10 digits, leading 1 -> KEEP
        ("phone: n/a", ""),
        ("", ""),
    ],
)
def test_phone(raw, expected):
    assert canonicalize(raw, "phone") == expected


# ── name ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  John   SMITH ", "john smith"),
        ("O'Brien", "obrien"),                    # punctuation deleted
        ("Smith-Jones", "smithjones"),
        ("Dr. Jane Doe, Jr.", "dr jane doe jr"),
        ("María José", "maría josé"),             # non-ASCII passes through
        ("", ""),
    ],
)
def test_name(raw, expected):
    assert canonicalize(raw, "name") == expected


# ── postal ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12345", "12345"),
        ("12345-6789", "12345"),                  # ZIP+4 -> first 5
        ("90210 ", "90210"),
        ("1234", "1234"),                         # fewer than 5 digits -> as-is
        ("SW1A 1AA", "SW1A1AA"),                  # UK: alnum-upper fallback
        ("k1a 0b1", "K1A0B1"),                    # CA: lowercased -> upper
        ("", ""),
    ],
)
def test_postal(raw, expected):
    assert canonicalize(raw, "postal") == expected


# ── cross-cutting guarantees ────────────────────────────────────────────────


_CORPUS = [
    "Alice@Example.COM",
    "  bob@x.org  ",
    "(555) 123-4567",
    "+1 555 123 4567",
    "O'Brien",
    "Dr. Jane Doe, Jr.",
    "María José",
    "12345-6789",
    "SW1A 1AA",
    "k1a 0b1",
    "",
    "   ",
    "!!!",
    "MixedCASE 123 -- text",
]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("raw", _CORPUS)
def test_idempotent(kind, raw):
    once = canonicalize(raw, kind)
    assert canonicalize(once, kind) == once


@pytest.mark.parametrize("kind", KINDS)
def test_none_maps_to_empty(kind):
    assert canonicalize(None, kind) == ""


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("raw", _CORPUS)
def test_returns_str_never_raises(kind, raw):
    out = canonicalize(raw, kind)
    assert isinstance(out, str)


def test_case_folding_is_ascii_only_not_locale_aware():
    # Locale-aware str.lower() folds the non-ASCII capitals (e.g. Turkish 'İ' ->
    # 'i̇'); ASCII-only must lowercase ONLY A-Z and leave the rest byte-identical.
    assert canonicalize("İSTANBUL", "name") == "İstanbul"  # 'İ' untouched
    assert canonicalize("ÉCLAIR", "name") == "Éclair"      # 'É' untouched


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        canonicalize("x", "zipcode")  # type: ignore[arg-type]


def test_exported_from_package_root():
    import goldenflow

    assert "canonicalize" in goldenflow.__all__
    assert goldenflow.canonicalize is canonicalize
