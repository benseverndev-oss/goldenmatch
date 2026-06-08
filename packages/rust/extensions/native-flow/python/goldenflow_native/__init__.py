"""goldenflow-native — optional Rust/PyO3 acceleration kernels for goldenflow.

This package ships ONLY the compiled abi3 ``_native`` extension. You don't
import it directly; ``goldenflow`` discovers it through
``goldenflow.core._native_loader`` when present and falls back to its pure-
Python paths when it isn't. Mirrors the polars / polars-runtime split and
goldenmatch-native: the frontend (``goldenflow``) stays a pure-Python wheel,
the compiled runtime ships separately and is pulled in via
``pip install goldenflow[native]``.
"""

from . import _native as _native  # the compiled abi3 extension module

__all__ = ["_native"]

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("goldenflow-native")
except PackageNotFoundError:  # source checkout without installed dist metadata
    __version__ = "0.1.1"
