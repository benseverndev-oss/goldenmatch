"""Loader + gate for the optional ``goldenflow._native`` acceleration module.

Mirrors ``goldenmatch.core._native_loader``. The native extension (Rust/PyO3,
built from ``packages/rust/extensions/native-flow``) is an *optional
accelerator*: when it isn't importable — or a component hasn't cleared the
parity gate — the pure-Python transform paths run unchanged.

``GOLDENFLOW_NATIVE`` env:
- ``"0"``    -> force the Python path (never use native).
- ``"1"``    -> require native; raise if it isn't importable (CI parity lane).
- ``"auto"`` / unset -> use native wherever a WIRED symbol exists for the
  component (``_has_symbol``), EXCEPT the known-divergent components in
  ``_FALLBACK_ONLY``. Default. All wired phone transforms (``phone_e164``,
  ``phone_country_code``, ``phone_national``) are canonical-NANP-gated and
  byte-identical to ``phonenumbers`` over the corpus, so both ``auto`` and ``=1``
  are output-faithful. (``phone_validate`` stays pure-Python -- its only native
  symbol, ``phone_valid_arrow``, implements ``is_valid``, NOT the product-chosen
  ``is_possible`` spec, so it is deliberately unwired and listed in
  ``_FALLBACK_ONLY``; see the goldenflow reference-mode spec. ``phone_digits``
  is pure Polars.)

The kernel is reachable two ways, tried in order, exactly like goldenmatch:
  1. ``goldenflow._native``        — in-tree build (scripts/build_native.py).
  2. ``goldenflow_native._native`` — the separately-distributed
     ``goldenflow-native`` abi3 wheel (``pip install goldenflow[native]``).
"""
from __future__ import annotations

import os
from typing import Any

try:
    import goldenflow._native as _native  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - any import/load failure falls back below
    try:
        from goldenflow_native import _native  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - neither path available -> pure Python
        _native = None


# Components whose native path has cleared parity and may run under
# ``GOLDENFLOW_NATIVE=auto``. Add a name here ONLY after a parity sign-off.
#
# Signed off 2026-06-07 (NANP-only gating):
#   - phone: the phone kernel runs in ``nanp_only`` mode (the Python bridge
#     passes it), so it emits a result ONLY for NANP numbers (country calling
#     code 1) and null for everything else. Characterization across 20 country
#     metadata sets showed the Rust ``phonenumber`` port is byte-identical to
#     the Python ``phonenumbers`` library EXCEPT when a ``+CC`` international
#     number is parsed with a mismatched default region ("US") and its national
#     number starts with "1" (e.g. ``+33142685300`` -> native ``+3342685300``):
#     the port mis-applies US national-prefix stripping. Those diverging
#     outputs are never country-code-1, so restricting native to NANP results
#     sidesteps the bug entirely; international rows fall back to the Python
#     reference. Parity asserted over a NANP residual corpus (alpha, extensions,
#     ambiguous leading-1, odd formats) AND a mixed intl corpus in
#     tests/transforms/test_native_parity.py.
_GATED_ON: frozenset[str] = frozenset({"phone"})

# Reference-mode (2026-07: Rust is the reference). Under ``auto`` the native
# kernel runs wherever a WIRED symbol exists for the component, EXCEPT the
# known-divergent components in ``_FALLBACK_ONLY``. ``_GATED_ON`` is retained
# only as documentation of the byte-exact surface; it no longer governs ``auto``.

# Floor symbols per component (wheel-skew safe: probe the actual module).
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "phone": ("phone_e164_arrow", "phone_national_arrow", "phone_country_code_arrow"),
    # NOTE: no "phone_validate" entry. Its only native symbol, phone_valid_arrow,
    # implements `is_valid`, NOT the product-chosen `is_possible` spec, so it is
    # deliberately unwired AND listed in _FALLBACK_ONLY below.
    # cc: payment-card (Luhn) identifiers -- floor symbol only, region-free.
    "cc": ("cc_validate_arrow",),
    # iban: IBAN (ISO 7064 mod-97) identifiers -- floor symbol only, region-free.
    "iban": ("iban_validate_arrow",),
    # isbn: ISBN-10/13 checksum identifiers -- floor symbol only, region-free.
    "isbn": ("isbn_validate_arrow",),
    # ean: EAN/UPC (GTIN mod-10) identifiers -- floor symbol only, region-free.
    "ean": ("ean_validate_arrow",),
    # swift: SWIFT/BIC (ISO 9362, structural only -- no checksum) --
    # floor symbol only, region-free.
    "swift": ("swift_validate_arrow",),
    # vat: EU VAT identifiers (structural, all prefixes; checksum for DE/IT
    # only -- see the CHECKSUM COVERAGE note in transforms/identifiers.py) --
    # floor symbol only, region-free.
    "vat": ("vat_validate_arrow",),
    # aba: US ABA routing number (weighted checksum) -- floor symbol only,
    # region-free.
    "aba": ("aba_validate_arrow",),
    # imei: IMEI (Luhn checksum, reuses the same luhn_ok as cc) -- floor
    # symbol only, region-free.
    "imei": ("imei_validate_arrow",),
    # name_transliterate: explicit ASCII-fold map for common Latin-script
    # diacritics -- floor symbol only, locale-free.
    "name_transliterate": ("name_transliterate_arrow",),
    # name_script: Unicode-range script detection -- floor symbol only,
    # locale-free.
    "name_script": ("name_script_arrow",),
    # email: lowercase/normalize/extract_domain/validate -- floor symbol only
    # (email_validate_arrow), locale-free, region-free.
    "email": ("email_validate_arrow",),
    # url: normalize/extract_domain -- floor symbol only (url_normalize_arrow),
    # locale-free, region-free.
    "url": ("url_normalize_arrow",),
    # numeric: string->number parsers (currency/percentage/to_integer/
    # comma_decimal/scientific_to_decimal) + numeric-array ops (round/clamp/
    # abs_value/fill_zero) -- floor symbol only (currency_strip_arrow),
    # locale-free, region-free.
    "numeric": ("currency_strip_arrow",),
    # categorical: boolean_normalize/gender_standardize/null_standardize +
    # the shared category_normalize_key (used by category_standardize/
    # category_from_file's runtime-data mapping lookup) -- floor symbol only
    # (boolean_normalize_arrow), locale-free, region-free.
    "categorical": ("boolean_normalize_arrow",),
    # names_ext: the names-remainder family -- strip_titles/strip_suffixes/
    # name_proper/nickname_standardize/has_initial (scalar) + split_name/
    # split_name_reverse (pair) + merge_name (two-input) -- floor symbol only
    # (strip_titles_arrow), locale-free.
    "names_ext": ("strip_titles_arrow",),
    # address: the US-address family -- address_standardize/address_expand/
    # state_abbreviate/state_expand/zip_normalize/country_standardize/
    # unit_normalize (scalar) + split_address (1->4 quad) -- floor symbol only
    # (address_standardize_arrow), US-scoped/locale-free.
    "address": ("address_standardize_arrow",),
}

# Components whose only native path is intentionally non-authoritative (the
# native symbol exists but implements the wrong spec). Mirrors goldenmatch's
# _FALLBACK_ONLY={"sail_scoring"}.
_FALLBACK_ONLY: frozenset[str] = frozenset({"phone_validate"})


def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    syms = _COMPONENT_SYMBOLS.get(component)
    if not syms:
        return False
    return any(hasattr(_native, s) for s in syms)


def native_module() -> Any:
    """The imported native module, or ``None`` if unavailable. Guard call sites
    with ``native_enabled(...)`` first."""
    return _native


def native_available() -> bool:
    return _native is not None


def native_enabled(component: str) -> bool:
    """Whether to use the native kernel for ``component`` on this call."""
    mode = os.environ.get("GOLDENFLOW_NATIVE", "auto").lower()
    if mode == "0":
        return False
    if mode == "1":
        if _native is None:
            raise RuntimeError(
                "GOLDENFLOW_NATIVE=1 but goldenflow._native is not built/importable"
            )
        return True
    return (
        _native is not None
        and component not in _FALLBACK_ONLY
        and _has_symbol(component)
    )
