"""goldencheck-native -- optional Rust/PyO3 acceleration kernels for goldencheck.

This package ships ONLY the compiled abi3 ``_native`` extension. You don't
import it directly; ``goldencheck`` discovers it through
``goldencheck.core._native_loader`` when present and falls back to its pure-
Python paths when it isn't. Mirrors goldenmatch's native / goldenmatch-native
split: the frontend (``goldencheck``) stays a pure-Python wheel, the compiled
runtime ships separately and is pulled in via ``pip install goldencheck[native]``.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldencheck_native/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldencheck_native.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://docs.bensevern.dev/goldencheck -- full docs.
  * ``goldencheck/llms.txt`` -- the host package this wheel serves, same idiom.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
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
