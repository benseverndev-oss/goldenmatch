"""goldenmatch-native — optional Rust/PyO3 acceleration kernels for goldenmatch.

This package ships ONLY the compiled abi3 `_native` extension. You don't import
it directly; `goldenmatch` discovers it through
``goldenmatch.core._native_loader`` when present and falls back to its pure-
Python paths when it isn't. Mirrors the polars / polars-runtime split: the
frontend (`goldenmatch`) stays a pure-Python wheel, the compiled runtime ships
separately and is pulled in via ``pip install goldenmatch[native]``.
"""

from . import _native as _native  # the compiled abi3 extension module

__all__ = ["_native"]

# Read the version from the installed distribution metadata (maturin sets it
# from pyproject `[project].version`) so it can never drift from the wheel --
# a hardcoded literal reported 0.1.0 on the 0.1.2 wheel (issue #688 follow-up).
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("goldenmatch-native")
except PackageNotFoundError:  # source checkout without installed dist metadata
    __version__ = "0.1.3"
