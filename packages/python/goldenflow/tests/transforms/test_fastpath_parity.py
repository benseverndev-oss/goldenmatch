"""Parity gate for the column transforms' owned-scalar reference.

The date family no longer has a Polars fast path OR a dateutil reference: the
owned deterministic parser (`goldenflow-core::dates`; the pure-Python
`_date_*_py` fallback) is the single source of truth. This gate asserts the
`series`-mode date transforms produce output byte-identical to their owned
per-row scalar over a large mixed corpus (well-formed + ambiguous + junk rows),
so the Polars `series` path and the Polars-free columnar `scalar=` path can
never diverge. The phone transforms still have a vectorized fast path checked
against the `phonenumbers` reference (a runtime dep).
"""
from __future__ import annotations

import random

import phonenumbers
import polars as pl
from goldenflow.transforms.dates import (
    _date_eu_py,
    _date_iso8601_py,
    _date_us_py,
    date_eu,
    date_iso8601,
    date_us,
)
from goldenflow.transforms.phone import phone_digits, phone_e164

# --- references: the OWNED deterministic per-row implementations --------------
# The date family is the deterministic source of truth (missing fields fill to
# Jan 1, 2-digit years use the POSIX pivot, exotic tail -> passthrough). The parity
# references ARE those owned scalars, so the `series` path is checked against the
# same deterministic reference the columnar/native path runs.

def _ref_iso(val):
    return _date_iso8601_py(val)


def _ref_us(val):
    return _date_us_py(val)


def _ref_eu(val):
    return _date_eu_py(val)


def _ref_e164(val):
    if val is None:
        return None
    try:
        parsed = phonenumbers.parse(val, "US")
    except phonenumbers.NumberParseException:
        return val
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _ref_digits(val):
    if val is None:
        return None
    return "".join(c for c in val if c.isdigit())


# --- corpora ----------------------------------------------------------------

def _date_corpus(n=5000):
    rng = random.Random(7)
    rows = []
    for _ in range(n):
        kind = rng.random()
        if kind < 0.20:
            rows.append(f"{rng.randint(1,12):02d}/{rng.randint(1,28):02d}/{rng.randint(1950,2024)}")
        elif kind < 0.40:
            rows.append(f"{rng.randint(1950,2024)}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}")
        elif kind < 0.55:
            m = rng.choice(["Jan","Feb","March","April","May","June","Jul","August","Sep","Oct","Nov","Dec"])
            rows.append(f"{m} {rng.randint(1,28)}, {rng.randint(1950,2024)}")
        elif kind < 0.65:
            rows.append(f"{rng.randint(1950,2024)}/{rng.randint(1,12):02d}/{rng.randint(1,28):02d}")
        elif kind < 0.75:
            # 2-digit year -> must fall through to dateutil (not in fast formats)
            rows.append(f"{rng.randint(1,12):02d}/{rng.randint(1,28):02d}/{rng.randint(0,99):02d}")
        elif kind < 0.85:
            # ambiguous / day-first-looking -> dateutil decides
            rows.append(f"{rng.randint(13,31)}/{rng.randint(1,12):02d}/{rng.randint(1950,2024)}")
        elif kind < 0.92:
            rows.append(rng.choice(["not a date", "", "tbd", "2024", "13/13/2024", "Feb 30, 2021"]))
        else:
            rows.append(None)
    return rows


def _phone_corpus(n=5000):
    rng = random.Random(11)
    rows = []
    for _ in range(n):
        kind = rng.random()
        area = rng.randint(200, 999)
        rest = rng.randint(2000000, 9999999)
        if kind < 0.30:
            rows.append(f"({area}) {str(rest)[:3]}-{str(rest)[3:]}")
        elif kind < 0.50:
            rows.append(f"{area}.{str(rest)[:3]}.{str(rest)[3:]}")
        elif kind < 0.65:
            rows.append(f"+1-{area}-{str(rest)[:3]}-{str(rest)[3:]}")
        elif kind < 0.75:
            rows.append(f"1{area}{rest}")
        elif kind < 0.83:
            # leading-1 10-digit + intl + junk -> phonenumbers decides
            rows.append(rng.choice(["1234567890", "+44 20 7946 0958", "+61 412 345 678"]))
        elif kind < 0.90:
            rows.append(rng.choice(["555-CALL-NOW", "1-800-FLOWERS", "x123", "invalid", ""]))
        else:
            rows.append(None)
    return rows


# --- the parity assertions --------------------------------------------------

def test_date_iso8601_parity():
    vals = _date_corpus()
    s = pl.Series("d", vals, dtype=pl.Utf8)
    got = date_iso8601(s).to_list()
    expected = [_ref_iso(v) for v in vals]
    assert got == expected


def test_date_us_parity():
    vals = _date_corpus()
    s = pl.Series("d", vals, dtype=pl.Utf8)
    assert date_us(s).to_list() == [_ref_us(v) for v in vals]


def test_date_eu_parity():
    vals = _date_corpus()
    s = pl.Series("d", vals, dtype=pl.Utf8)
    assert date_eu(s).to_list() == [_ref_eu(v) for v in vals]


def test_phone_e164_parity():
    vals = _phone_corpus()
    s = pl.Series("ph", vals, dtype=pl.Utf8)
    got = phone_e164(s).to_list()
    expected = [_ref_e164(v) for v in vals]
    assert got == expected


def test_phone_digits_parity():
    vals = _phone_corpus()
    s = pl.Series("ph", vals, dtype=pl.Utf8)
    assert phone_digits(s).to_list() == [_ref_digits(v) for v in vals]


def test_phone_e164_international_not_misnanped(monkeypatch):
    """Regression: an int'l +CC number can strip to exactly 10 digits starting
    2-9 (e.g. German +4930123456 -> "4930123456"). Without the no-"+" guard the
    Polars fast path would emit +14930123456. Pinned to the pure-Python path
    (native off) so it isolates the Tier-1 fast path."""
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    vals = ["+4930123456", "+49 30 123456", "+33142685300", "+1-201-555-0123", "(201) 555-0123"]
    got = phone_e164(pl.Series("ph", vals)).to_list()
    assert got == [_ref_e164(v) for v in vals]
