"""goldencheck-native -- optional Rust/PyO3 acceleration kernels for goldencheck.

This package ships ONLY the compiled abi3 ``_native`` extension. You don't
import it directly; ``goldencheck`` discovers it through
``goldencheck.core._native_loader`` when present and falls back to its pure-
Python paths when it isn't. Mirrors goldenmatch's native / goldenmatch-native
split: the frontend (``goldencheck``) stays a pure-Python wheel, the compiled
runtime ships separately and is pulled in via ``pip install goldencheck[native]``.
"""

from . import _native as _native  # the compiled abi3 extension module

__all__ = ["_native"]

# Read the version from the installed distribution metadata (maturin sets it
# from pyproject `[project].version`) so it can never drift from the wheel.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("goldencheck-native")
except PackageNotFoundError:  # source checkout without installed dist metadata
    __version__ = "0.1.0"
