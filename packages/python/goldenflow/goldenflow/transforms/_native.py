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


def phone_country_code_native() -> Callable[[pl.Series], pl.Series] | None:
    # Safe under nanp_only: native and phonenumbers agree on the country code
    # (1) for every NANP row; the leading-1 ambiguity only affects the national
    # number, not the code. International rows come back null -> Python.
    return _kernel_runner("phone_country_code_arrow")
