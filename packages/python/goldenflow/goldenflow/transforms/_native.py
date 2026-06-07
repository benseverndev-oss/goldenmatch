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
        out = func(s.to_arrow(), _DEFAULT_REGION)
        return pl.from_arrow(out)

    return run


def phone_e164_native() -> Callable[[pl.Series], pl.Series] | None:
    return _kernel_runner("phone_e164_arrow")


def phone_national_native() -> Callable[[pl.Series], pl.Series] | None:
    return _kernel_runner("phone_national_arrow")


def phone_country_code_native() -> Callable[[pl.Series], pl.Series] | None:
    return _kernel_runner("phone_country_code_arrow")


def phone_valid_native() -> Callable[[pl.Series], pl.Series] | None:
    return _kernel_runner("phone_valid_arrow")
