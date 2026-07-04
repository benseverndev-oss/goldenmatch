"""Thin Arrow bridge from the Polars Series transforms to the goldenflow-native
phone kernels.

Each helper returns ``None`` when the native path is not in play — kernel not
built, component not gated (see ``goldenflow.core._native_loader``), or pyarrow
absent — so callers simply pass it to ``apply_with_residual`` as the optional
``native_fn`` and the Python reference handles everything when it's ``None``.

The Series <-> Arrow round-trip is zero-copy: ``Series.to_arrow()`` hands the
kernel the underlying Arrow buffer, and ``pl.from_arrow`` wraps the result back
without materializing Python objects (the thing that makes ``map_elements``
slow). Returns null for any row the kernel can't resolve; tier 3 settles those.
"""
from __future__ import annotations

from collections.abc import Callable

import polars as pl

from goldenflow.core._native_loader import native_enabled, native_module

_DEFAULT_REGION = "US"


def _as_str_series(s: pl.Series) -> pl.Series:
    """Ensure a Utf8 series for the Arrow bridge. An all-null column (or a
    single ``[None]``) is Null-dtype in Polars, and ``.to_arrow()`` on it yields
    a Null Arrow array the kernels reject (``expected an Arrow Utf8 or LargeUtf8
    array``). Cast to Utf8 (null-preserving) so the native path handles nulls the
    same way the pure-Python fallback does. Utf8 input is returned unchanged to
    keep the round-trip zero-copy."""
    return s if s.dtype == pl.Utf8 else s.cast(pl.Utf8)


def _kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a ``native_fn`` for kernel function ``attr`` if native phone is
    enabled and the dependencies are importable; else ``None``."""
    if not native_enabled("phone"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        # nanp_only=True: the kernel emits a result ONLY for NANP (country code
        # 1) numbers, where it is byte-identical to the phonenumbers library;
        # international rows come back null and tier-3 Python settles them. This
        # is what makes `phone` safe to keep in _native_loader._GATED_ON.
        out = func(s.to_arrow(), _DEFAULT_REGION, True)
        return pl.from_arrow(out)

    return run


# Canonical NANP E.164: "+1" + 10-digit national number, area code 2-9. The
# kernel's nanp_only mode already restricts to country code 1, but native still
# diverges from phonenumbers on ambiguous leading-1 inputs (e.g. "1234567890"
# -> native "+1234567890" with a 9-digit national number). Those non-canonical
# outputs are nulled here so tier-3 Python settles them; only well-formed NANP
# E.164 (where native == phonenumbers, proven over corpus) is accepted.
_CANONICAL_NANP = r"^\+1[2-9]\d{9}$"


def phone_e164_native() -> Callable[[pl.Series], pl.Series] | None:
    inner = _kernel_runner("phone_e164_arrow")
    if inner is None:
        return None

    def run(s: pl.Series) -> pl.Series:
        out = inner(s)
        # Keep only canonical NANP E.164; null the rest for the Python fallback.
        return out.set(~out.str.contains(_CANONICAL_NANP).fill_null(False), None)

    return run


# Canonical NANP NATIONAL format: "(NXX) NXX-XXXX", area/exchange codes 2-9 (a
# valid NANP number). The same gate idea as E.164: native and phonenumbers agree
# on this well-formed shape; the ambiguous leading-1 inputs the loader comment
# worried about produce a non-canonical shape (or a 1-leading area code) that
# fails this regex and is nulled for tier-3 Python to settle. So national IS
# gate-able after all -- the residual is just narrower than E.164's.
_CANONICAL_NANP_NATIONAL = r"^\([2-9]\d{2}\) [2-9]\d{2}-\d{4}$"


def phone_national_native() -> Callable[[pl.Series], pl.Series] | None:
    inner = _kernel_runner("phone_national_arrow")
    if inner is None:
        return None

    def run(s: pl.Series) -> pl.Series:
        out = inner(s)
        # Keep only canonical NANP national format; null the rest for Python.
        return out.set(
            ~out.str.contains(_CANONICAL_NANP_NATIONAL).fill_null(False), None
        )

    return run


def phone_country_code_native() -> Callable[[pl.Series], pl.Series] | None:
    # Safe under nanp_only: native and phonenumbers agree on the country code
    # (1) for every NANP row; the leading-1 ambiguity only affects the national
    # number, not the code. International rows come back null -> Python.
    return _kernel_runner("phone_country_code_arrow")


def _cc_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for card-identifier kernel function ``attr``
    if native ``cc`` is enabled and the dependencies are importable; else
    ``None``. Unlike ``_kernel_runner`` (phone), the card kernel takes no
    region/gating args -- Luhn is region-free."""
    if not native_enabled("cc"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def cc_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _cc_kernel_runner("cc_validate_arrow")


def cc_format_native() -> Callable[[pl.Series], pl.Series] | None:
    return _cc_kernel_runner("cc_format_arrow")


def cc_mask_native() -> Callable[[pl.Series], pl.Series] | None:
    return _cc_kernel_runner("cc_mask_arrow")


def _iban_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for IBAN kernel function ``attr`` if
    native ``iban`` is enabled and the dependencies are importable; else
    ``None``. Like ``_cc_kernel_runner`` -- no region/gating args, the mod-97
    check is region-free."""
    if not native_enabled("iban"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def iban_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _iban_kernel_runner("iban_validate_arrow")


def iban_format_native() -> Callable[[pl.Series], pl.Series] | None:
    return _iban_kernel_runner("iban_format_arrow")


def _isbn_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for ISBN kernel function ``attr`` if
    native ``isbn`` is enabled and the dependencies are importable; else
    ``None``. Like ``_cc_kernel_runner``/``_iban_kernel_runner`` -- no
    region/gating args, the checksum is region-free."""
    if not native_enabled("isbn"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def isbn_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _isbn_kernel_runner("isbn_validate_arrow")


def isbn_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _isbn_kernel_runner("isbn_normalize_arrow")


def _ean_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for EAN/UPC kernel function ``attr`` if
    native ``ean`` is enabled and the dependencies are importable; else
    ``None``. Like ``_cc_kernel_runner``/``_iban_kernel_runner``/
    ``_isbn_kernel_runner`` -- no region/gating args, the GTIN checksum is
    region-free."""
    if not native_enabled("ean"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def ean_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _ean_kernel_runner("ean_validate_arrow")


def _swift_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for SWIFT/BIC kernel function ``attr`` if
    native ``swift`` is enabled and the dependencies are importable; else
    ``None``. Like the other identifier runners -- no region/gating args,
    SWIFT/BIC validation is purely structural (no checksum)."""
    if not native_enabled("swift"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def swift_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _swift_kernel_runner("swift_validate_arrow")


def swift_format_native() -> Callable[[pl.Series], pl.Series] | None:
    return _swift_kernel_runner("swift_format_arrow")


def _vat_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for EU VAT kernel function ``attr`` if
    native ``vat`` is enabled and the dependencies are importable; else
    ``None``. Like ``_ean_kernel_runner`` -- no region/gating args, the
    structural + DE/IT checksum checks are region-free (the region is
    encoded in the input's own country prefix)."""
    if not native_enabled("vat"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def vat_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _vat_kernel_runner("vat_validate_arrow")


def vat_format_native() -> Callable[[pl.Series], pl.Series] | None:
    return _vat_kernel_runner("vat_format_arrow")


def _aba_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for ABA routing-number kernel function
    ``attr`` if native ``aba`` is enabled and the dependencies are
    importable; else ``None``. Like the other identifier runners -- no
    region/gating args, the checksum is region-free."""
    if not native_enabled("aba"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def aba_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _aba_kernel_runner("aba_validate_arrow")


def _imei_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for IMEI kernel function ``attr`` if
    native ``imei`` is enabled and the dependencies are importable; else
    ``None``. Like the other identifier runners -- no region/gating args,
    the Luhn checksum is region-free."""
    if not native_enabled("imei"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> pl.Series:
        return pl.from_arrow(func(_as_str_series(s).to_arrow()))

    return run


def imei_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _imei_kernel_runner("imei_validate_arrow")
