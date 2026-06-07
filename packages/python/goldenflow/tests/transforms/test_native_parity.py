"""Parity gate for the goldenflow-native phone kernel under NANP-only gating.

Runs only when the ``goldenflow-native`` wheel (or in-tree build) is importable;
otherwise skips. With the kernel in ``nanp_only`` mode plus the canonical-NANP
acceptance check in ``transforms/_native.py``, ``phone`` is in
``_native_loader._GATED_ON`` and runs by default under ``GOLDENFLOW_NATIVE=auto``.

The contract these tests lock in:
* the kernel only ever emits NANP (country-code-1) results in ``nanp_only`` mode;
* the end-to-end transforms (which gate further on canonical NANP E.164) are
  byte-identical to the pure ``phonenumbers`` reference over a comprehensive
  corpus — clean/alpha/extension/ambiguous NANP plus many international formats;
* native actually resolves part of the residual (the gate isn't a silent no-op).
"""
from __future__ import annotations

import random

import phonenumbers
import polars as pl
import pytest
from goldenflow.core._native_loader import native_available, native_enabled, native_module

if not native_available():
    pytest.skip("goldenflow-native not built/importable", allow_module_level=True)

# pyarrow is only needed on the native path (the [native] extra pulls it). Skip
# rather than error if it's absent, and import only after the native-available
# gate so the pure-Python matrix lane never trips on it.
pa = pytest.importorskip("pyarrow")

from goldenflow.transforms._native import phone_e164_native  # noqa: E402
from goldenflow.transforms.phone import phone_country_code, phone_e164  # noqa: E402

# Use whichever kernel the loader resolved (in-tree build shadows the wheel).
_native = native_module()


def _ref_e164(v):
    if v is None:
        return None
    try:
        p = phonenumbers.parse(v, "US")
    except phonenumbers.NumberParseException:
        return v  # phone_e164 preserves the original string on parse failure
    return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)


def _ref_cc(v):
    if v is None:
        return None
    try:
        p = phonenumbers.parse(v, "US")
    except phonenumbers.NumberParseException:
        return None
    return p.country_code


def _corpus(n=20000):
    rng = random.Random(2024)
    intl = []
    for r in ["GB", "FR", "DE", "AU", "JP", "IN", "BR", "MX", "ES", "IT",
              "NL", "SE", "CN", "RU", "ZA", "NG", "KE", "AR", "CH", "TR"]:
        ex = phonenumbers.example_number(r)
        if ex:
            for fmt in (phonenumbers.PhoneNumberFormat.E164,
                        phonenumbers.PhoneNumberFormat.INTERNATIONAL,
                        phonenumbers.PhoneNumberFormat.NATIONAL):
                intl.append(phonenumbers.format_number(ex, fmt))
    rows = []
    for _ in range(n):
        k = rng.random()
        a, rest = rng.randint(200, 999), rng.randint(2000000, 9999999)
        if k < 0.22:
            rows.append(f"({a}) {str(rest)[:3]}-{str(rest)[3:]}")
        elif k < 0.34:
            rows.append(f"+1-{a}-{str(rest)[:3]}-{str(rest)[3:]}")
        elif k < 0.44:
            rows.append(rng.choice(["1-800-FLOWERS", "1-800-GOT-JUNK", "555-CALL-NOW"]))
        elif k < 0.52:
            rows.append(f"{a}{rest}x{rng.randint(10, 9999)}")
        elif k < 0.62:
            rows.append(rng.choice(["1234567890", "11234567890", "18005550199", "01234567890"]))
        elif k < 0.86:
            rows.append(rng.choice(intl))
        elif k < 0.96:
            rows.append(rng.choice(["invalid", "", "x", "++", "n/a", None]))
        else:
            rows.append(None)
    return rows


def test_kernel_nanp_only_emits_only_country_code_1():
    """In nanp_only mode the kernel returns a value only for NANP numbers."""
    intl = ["+44 20 7946 0958", "+33142685300", "+49 30 123456", "+61 412 345 678"]
    nanp = ["(201) 555-0123", "+1-305-555-0199", "8005550199"]
    out = _native.phone_e164_arrow(pa.array(intl + nanp, type=pa.string()), "US", True).to_pylist()
    assert out[: len(intl)] == [None] * len(intl)          # international -> null
    assert all(v is not None and v.startswith("+1") for v in out[len(intl):])


def test_phone_e164_native_gated_on_by_default(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    assert native_enabled("phone") is True
    assert phone_e164_native() is not None


def test_phone_e164_parity_with_native(monkeypatch):
    """End-to-end E.164 == pure phonenumbers across the full corpus, native on."""
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    vals = _corpus()
    got = phone_e164(pl.Series("ph", vals)).to_list()
    assert got == [_ref_e164(v) for v in vals]


def test_phone_country_code_parity_with_native(monkeypatch):
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    vals = _corpus()
    got = phone_country_code(pl.Series("ph", vals)).to_list()
    assert got == [_ref_cc(v) for v in vals]


def test_native_actually_resolves_residual(monkeypatch):
    """Guard against the gate becoming a silent no-op: native must resolve the
    canonical-NANP residual the Polars fast path can't (alpha / extension)."""
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    runner = phone_e164_native()
    assert runner is not None
    resolved = runner(pl.Series("ph", ["1-800-FLOWERS", "2015550123x99"]))
    assert resolved.to_list() == ["+18003569377", "+12015550123"]
