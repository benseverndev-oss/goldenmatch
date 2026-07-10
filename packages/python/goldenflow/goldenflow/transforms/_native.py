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

from goldenflow._polars_lazy import pl
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


def _str_kernel_runner(component: str, attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Generic whole-series runner for a region-free ``str -> str`` kernel
    ``attr`` gated on ``component`` (same shape as ``_cc_kernel_runner``)."""
    if not native_enabled(component):
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


def ssn_format_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("us_id", "ssn_format_arrow")


def ssn_mask_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("us_id", "ssn_mask_arrow")


def ein_format_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("us_id", "ein_format_arrow")


def phone_digits_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("phone_digits", "phone_digits_arrow")


def soundex_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("phonetic", "soundex_arrow")


# --- W5 breadth kernels (str in, bool/str/int/float out via _str_kernel_runner) ---


def isin_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("isin", "isin_validate_arrow")


def cusip_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("cusip", "cusip_validate_arrow")


def npi_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("npi", "npi_validate_arrow")


def luhn_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("luhn", "luhn_validate_arrow")


def cc_brand_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("cc", "cc_brand_arrow")


def name_initials_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("names_ext", "name_initials_arrow")


def strip_middle_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("names_ext", "strip_middle_arrow")


def roman_to_int_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("numeric", "roman_to_int_arrow")


def ordinal_to_int_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("numeric", "ordinal_to_int_arrow")


def fraction_to_decimal_native() -> Callable[[pl.Series], pl.Series] | None:
    return _str_kernel_runner("numeric", "fraction_to_decimal_arrow")


def double_metaphone_native() -> (
    Callable[[pl.Series], tuple[pl.Series, pl.Series]] | None
):
    """Build a runner for the multi-output double_metaphone kernel: one string
    array in, a PAIR of arrays (primary, alternate) out. ``None`` when native
    ``phonetic`` is off or unbuilt. Mirrors ``split_name_native``."""
    if not native_enabled("phonetic"):
        return None
    nm = native_module()
    attr = "double_metaphone_arrow"
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> tuple[pl.Series, pl.Series]:
        primary_arr, alt_arr = func(_as_str_series(s).to_arrow())
        return pl.from_arrow(primary_arr), pl.from_arrow(alt_arr)

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


def _name_kernel_runner(
    component: str, attr: str
) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for name kernel function ``attr`` if
    native ``component`` is enabled and the dependencies are importable;
    else ``None``. Like the identifier runners -- no region/gating args,
    the transliteration map / script-range tables are locale-free."""
    if not native_enabled(component):
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


def name_transliterate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _name_kernel_runner("name_transliterate", "name_transliterate_arrow")


def name_script_native() -> Callable[[pl.Series], pl.Series] | None:
    return _name_kernel_runner("name_script", "name_script_arrow")


def _names_ext_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a names-remainder kernel function
    ``attr`` (strip_titles/strip_suffixes/name_proper/nickname_standardize/
    has_initial) if native ``names_ext`` is enabled and importable; else
    ``None``. Single string array in, one array out (str or bool)."""
    if not native_enabled("names_ext"):
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


def strip_titles_native() -> Callable[[pl.Series], pl.Series] | None:
    return _names_ext_kernel_runner("strip_titles_arrow")


def strip_suffixes_native() -> Callable[[pl.Series], pl.Series] | None:
    return _names_ext_kernel_runner("strip_suffixes_arrow")


def name_proper_native() -> Callable[[pl.Series], pl.Series] | None:
    return _names_ext_kernel_runner("name_proper_arrow")


def nickname_standardize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _names_ext_kernel_runner("nickname_standardize_arrow")


def has_initial_native() -> Callable[[pl.Series], pl.Series] | None:
    return _names_ext_kernel_runner("has_initial_arrow")


def _split_name_runner(
    attr: str,
) -> Callable[[pl.Series], tuple[pl.Series, pl.Series]] | None:
    """Build a runner for the multi-output split kernels (split_name /
    split_name_reverse): one string array in, a PAIR of arrays (first, last)
    out. ``None`` when native ``names_ext`` is off or unbuilt."""
    if not native_enabled("names_ext"):
        return None
    nm = native_module()
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(s: pl.Series) -> tuple[pl.Series, pl.Series]:
        first_arr, last_arr = func(_as_str_series(s).to_arrow())
        return pl.from_arrow(first_arr), pl.from_arrow(last_arr)

    return run


def split_name_native() -> Callable[[pl.Series], tuple[pl.Series, pl.Series]] | None:
    return _split_name_runner("split_name_arrow")


def split_name_reverse_native() -> (
    Callable[[pl.Series], tuple[pl.Series, pl.Series]] | None
):
    return _split_name_runner("split_name_reverse_arrow")


def merge_name_native() -> Callable[[pl.Series, pl.Series], pl.Series] | None:
    """Build a runner for merge_name: TWO string arrays (first, last) in, one
    ``full_name`` array out. ``None`` when native ``names_ext`` is off/unbuilt."""
    if not native_enabled("names_ext"):
        return None
    nm = native_module()
    attr = "merge_name_arrow"
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(first: pl.Series, last: pl.Series) -> pl.Series:
        return pl.from_arrow(
            func(_as_str_series(first).to_arrow(), _as_str_series(last).to_arrow())
        )

    return run


def _address_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a scalar address kernel function ``attr``
    (address_standardize/address_expand/state_abbreviate/state_expand/
    zip_normalize/country_standardize/unit_normalize) if native ``address`` is
    enabled and importable; else ``None``. Single string array in, one out.
    The in-crate street/state/country tables are locale-free (US-scoped)."""
    if not native_enabled("address"):
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


def address_standardize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("address_standardize_arrow")


def address_expand_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("address_expand_arrow")


def state_abbreviate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("state_abbreviate_arrow")


def state_expand_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("state_expand_arrow")


def zip_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("zip_normalize_arrow")


def country_standardize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("country_standardize_arrow")


def unit_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _address_kernel_runner("unit_normalize_arrow")


def split_address_native() -> (
    Callable[[pl.Series], tuple[pl.Series, pl.Series, pl.Series, pl.Series]] | None
):
    """Build a runner for the multi-output split_address kernel: one string
    array in, a QUAD of arrays (street, city, state, zip) out. ``None`` when
    native ``address`` is off or unbuilt."""
    if not native_enabled("address"):
        return None
    nm = native_module()
    attr = "split_address_arrow"
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(
        s: pl.Series,
    ) -> tuple[pl.Series, pl.Series, pl.Series, pl.Series]:
        street_arr, city_arr, state_arr, zip_arr = func(_as_str_series(s).to_arrow())
        return (
            pl.from_arrow(street_arr),
            pl.from_arrow(city_arr),
            pl.from_arrow(state_arr),
            pl.from_arrow(zip_arr),
        )

    return run


def _text_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a scalar text kernel function ``attr``
    (strip/collapse_whitespace/normalize_quotes/normalize_line_endings/
    remove_html_tags/remove_urls/remove_digits/remove_punctuation/
    remove_emojis/extract_numbers) if native ``text`` is enabled and
    importable; else ``None``. Single string array in, one out."""
    if not native_enabled("text"):
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


def strip_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("strip_arrow")


def collapse_whitespace_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("collapse_whitespace_arrow")


def normalize_quotes_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("normalize_quotes_arrow")


def normalize_line_endings_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("normalize_line_endings_arrow")


def remove_html_tags_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("remove_html_tags_arrow")


def remove_urls_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("remove_urls_arrow")


def remove_digits_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("remove_digits_arrow")


def remove_punctuation_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("remove_punctuation_arrow")


def remove_emojis_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("remove_emojis_arrow")


def extract_numbers_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("extract_numbers_arrow")


def _text_param_kernel_runner(
    attr: str, **kwargs: int | str
) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a PARAMETERIZED text kernel function
    ``attr`` (truncate/pad_left/pad_right) if native ``text`` is enabled and
    importable; else ``None``. The per-column-constant params (``n`` for
    truncate, ``width``/``pad`` for the pads) are forwarded as kwargs to the
    kernel call, mirroring the numeric ``round``/``clamp`` runners."""
    if not native_enabled("text"):
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
        return pl.from_arrow(func(_as_str_series(s).to_arrow(), **kwargs))

    return run


def truncate_native(n: int = 255) -> Callable[[pl.Series], pl.Series] | None:
    return _text_param_kernel_runner("truncate_arrow", n=n)


def pad_left_native(
    width: int = 10, pad: str = "0"
) -> Callable[[pl.Series], pl.Series] | None:
    return _text_param_kernel_runner("pad_left_arrow", width=width, pad=pad)


def pad_right_native(
    width: int = 10, pad: str = " "
) -> Callable[[pl.Series], pl.Series] | None:
    return _text_param_kernel_runner("pad_right_arrow", width=width, pad=pad)


def lowercase_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("lowercase_arrow")


def uppercase_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("uppercase_arrow")


def title_case_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("title_case_arrow")


def normalize_unicode_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("normalize_unicode_arrow")


def fix_mojibake_native() -> Callable[[pl.Series], pl.Series] | None:
    return _text_kernel_runner("fix_mojibake_arrow")


def build_canonical_map_native(
    freq_threshold: float = 0.05, match_threshold: float = 85.0
) -> Callable[[pl.Series, pl.Series], tuple[pl.Series, pl.Series]] | None:
    """Build a runner for the data-dependent category-autocorrect kernel: a
    ``values`` (Utf8) + ``counts`` (Int64) pair in, a ``(from, to)`` pair of
    Utf8 Series out (the corrections map). ``None`` when native ``autocorrect``
    is off/unbuilt. The thresholds are bound at construction (per-call params)."""
    if not native_enabled("autocorrect"):
        return None
    nm = native_module()
    attr = "build_canonical_map_arrow"
    if nm is None or not hasattr(nm, attr):
        return None
    try:
        import pyarrow  # noqa: F401  (zero-copy bridge)
    except ImportError:
        return None
    func = getattr(nm, attr)

    def run(values: pl.Series, counts: pl.Series) -> tuple[pl.Series, pl.Series]:
        from_arr, to_arr = func(
            _as_str_series(values).to_arrow(),
            counts.cast(pl.Int64).to_arrow(),
            freq_threshold=freq_threshold,
            match_threshold=match_threshold,
        )
        return pl.from_arrow(from_arr), pl.from_arrow(to_arr)

    return run


def _company_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a company kernel function ``attr``
    (company_normalize/company_strip_legal/company_extract_legal) if native
    ``company`` is enabled and importable; else ``None``. Single string array
    in, one out; the legal-suffix table is locale-free."""
    if not native_enabled("company"):
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


def company_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _company_kernel_runner("company_normalize_arrow")


def company_strip_legal_native() -> Callable[[pl.Series], pl.Series] | None:
    return _company_kernel_runner("company_strip_legal_arrow")


def company_extract_legal_native() -> Callable[[pl.Series], pl.Series] | None:
    return _company_kernel_runner("company_extract_legal_arrow")


def _email_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for email kernel function ``attr`` if
    native ``email`` is enabled and the dependencies are importable; else
    ``None``. Like the other identifier runners -- no region/gating args,
    lowercase/normalize/domain-extract/validate are all locale-free."""
    if not native_enabled("email"):
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


def email_lowercase_native() -> Callable[[pl.Series], pl.Series] | None:
    return _email_kernel_runner("email_lowercase_arrow")


def email_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _email_kernel_runner("email_normalize_arrow")


def email_canonical_native() -> Callable[[pl.Series], pl.Series] | None:
    return _email_kernel_runner("email_canonical_arrow")


def email_mask_native() -> Callable[[pl.Series], pl.Series] | None:
    return _email_kernel_runner("email_mask_arrow")


def email_extract_domain_native() -> Callable[[pl.Series], pl.Series] | None:
    return _email_kernel_runner("email_extract_domain_arrow")


def email_validate_native() -> Callable[[pl.Series], pl.Series] | None:
    return _email_kernel_runner("email_validate_arrow")


def _url_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for URL kernel function ``attr`` if
    native ``url`` is enabled and the dependencies are importable; else
    ``None``. Like the other identifier runners -- no region/gating args,
    scheme/domain normalization and domain extraction are locale-free."""
    if not native_enabled("url"):
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


def url_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _url_kernel_runner("url_normalize_arrow")


def url_extract_domain_native() -> Callable[[pl.Series], pl.Series] | None:
    return _url_kernel_runner("url_extract_domain_arrow")


def url_strip_tracking_native() -> Callable[[pl.Series], pl.Series] | None:
    return _url_kernel_runner("url_strip_tracking_arrow")


def url_strip_www_native() -> Callable[[pl.Series], pl.Series] | None:
    return _url_kernel_runner("url_strip_www_arrow")


def url_canonical_native() -> Callable[[pl.Series], pl.Series] | None:
    return _url_kernel_runner("url_canonical_arrow")


def _as_f64_series(s: pl.Series) -> pl.Series:
    """Ensure a Float64 series for the numeric-array-op Arrow bridge (round/
    clamp/abs/fill_zero). Mirrors ``_as_str_series`` -- an all-null column is
    Null-dtype, whose ``.to_arrow()`` the kernels reject."""
    return s if s.dtype == pl.Float64 else s.cast(pl.Float64, strict=False)


def _numeric_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a numeric STRING-PARSER kernel function
    ``attr`` (currency/percentage/to_integer/comma_decimal/
    scientific_to_decimal) if native ``numeric`` is enabled and the
    dependencies are importable; else ``None``. No region/gating args --
    these parsers are locale-free (except comma_decimal's EU-format
    detection, which is baked into the kernel itself)."""
    if not native_enabled("numeric"):
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


def currency_strip_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_kernel_runner("currency_strip_arrow")


def percentage_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_kernel_runner("percentage_normalize_arrow")


def to_integer_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_kernel_runner("to_integer_arrow")


def comma_decimal_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_kernel_runner("comma_decimal_arrow")


def scientific_to_decimal_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_kernel_runner("scientific_to_decimal_arrow")


def _numeric_array_kernel_runner(
    attr: str, **kwargs: float | int
) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for a numeric ARRAY-OP kernel function
    ``attr`` (round/clamp/abs_value/fill_zero) if native ``numeric`` is
    enabled and the dependencies are importable; else ``None``. Unlike the
    string-parser runners, these read/write Float64 (not Utf8) Arrow arrays
    and may carry params (``n`` for round, ``min_val``/``max_val`` for
    clamp) forwarded as kwargs to the kernel call."""
    if not native_enabled("numeric"):
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
        return pl.from_arrow(func(_as_f64_series(s).to_arrow(), **kwargs))

    return run


def round_native(n: int = 2) -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_array_kernel_runner("round_arrow", n=n)


def clamp_native(
    min_val: float = 0.0, max_val: float = 1.0
) -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_array_kernel_runner(
        "clamp_arrow", min_val=min_val, max_val=max_val
    )


def abs_value_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_array_kernel_runner("abs_value_arrow")


def fill_zero_native() -> Callable[[pl.Series], pl.Series] | None:
    return _numeric_array_kernel_runner("fill_zero_arrow")


def _categorical_kernel_runner(attr: str) -> Callable[[pl.Series], pl.Series] | None:
    """Build a whole-series runner for categorical kernel function ``attr``
    if native ``categorical`` is enabled and the dependencies are
    importable; else ``None``. Like the other identifier runners -- no
    region/gating args, the fixed lookup tables (boolean/gender/null) and
    the key-normalization step are locale-free."""
    if not native_enabled("categorical"):
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


def boolean_normalize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _categorical_kernel_runner("boolean_normalize_arrow")


def gender_standardize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _categorical_kernel_runner("gender_standardize_arrow")


def null_standardize_native() -> Callable[[pl.Series], pl.Series] | None:
    return _categorical_kernel_runner("null_standardize_arrow")


def category_normalize_key_native() -> Callable[[pl.Series], pl.Series] | None:
    return _categorical_kernel_runner("category_normalize_key_arrow")
