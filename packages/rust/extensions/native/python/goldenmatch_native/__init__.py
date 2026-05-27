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
__version__ = "0.1.0"
