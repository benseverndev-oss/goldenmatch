"""Parity gate for the optional goldenflow-native phone kernel.

These tests run only when the ``goldenflow-native`` wheel (or in-tree build) is
importable; otherwise they skip. They document, in executable form, WHY the
phone kernel is not in ``_native_loader._GATED_ON``:

* On the NANP subset (region US, country code 1) the native E.164 output is
  byte-identical to the Python ``phonenumbers`` library.
* On some international national numbers it is NOT (the Rust ``phonenumber``
  port drops the national leading digit on e.g. French numbers), so enabling it
  by default would change cleaned values.

The end-to-end transform must therefore stay on the Python reference by default.
"""
from __future__ import annotations

import random

import phonenumbers
import polars as pl
import pytest

pytest.importorskip("goldenflow_native")
import pyarrow as pa  # noqa: E402
from goldenflow.transforms.phone import phone_e164  # noqa: E402
from goldenflow_native import _native  # noqa: E402


def _ref_e164(v):
    if v is None:
        return None
    try:
        p = phonenumbers.parse(v, "US")
    except phonenumbers.NumberParseException:
        return None
    return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)


def _nanp_corpus(n=8000):
    rng = random.Random(5)
    out = []
    for _ in range(n):
        area = rng.randint(200, 999)
        rest = f"{rng.randint(2000000, 9999999)}"
        out.append(rng.choice([
            f"({area}) {rest[:3]}-{rest[3:]}",
            f"+1-{area}-{rest[:3]}-{rest[3:]}",
            f"1{area}{rest}",
            f"{area}.{rest[:3]}.{rest[3:]}",
        ]))
    return out


def test_native_matches_python_on_nanp():
    """The parity-safe subset: native == phonenumbers byte-for-byte on NANP."""
    vals = _nanp_corpus()
    arr = pa.array(vals, type=pa.string())
    native = _native.phone_e164_arrow(arr, "US").to_pylist()
    expected = [_ref_e164(v) for v in vals]
    assert native == expected


def test_native_diverges_on_international():
    """Documents the open divergence that keeps phone out of _GATED_ON: the
    Rust port formats this French national number differently from the Python
    library. If a future metadata alignment closes this, update _GATED_ON."""
    arr = pa.array(["+33 1 42 68 53 00"], type=pa.string())
    native = _native.phone_e164_arrow(arr, "US").to_pylist()[0]
    python = _ref_e164("+33 1 42 68 53 00")
    assert native != python  # known divergence; native drops the national "1"


def test_transform_default_is_pure_python_parity():
    """With the wheel installed but GOLDENFLOW_NATIVE unset (auto), phone is NOT
    gated, so the transform output equals the pure phonenumbers reference even
    on the international cases."""
    def ref_preserve(v):
        # phone_e164 preserves the original string on parse failure.
        if v is None:
            return None
        out = _ref_e164(v)
        return out if out is not None else v

    vals = ["+33 1 42 68 53 00", "(212) 555-0184", "+44 20 7946 0958", None, "invalid"]
    got = phone_e164(pl.Series("ph", vals)).to_list()
    assert got == [ref_preserve(v) for v in vals]
