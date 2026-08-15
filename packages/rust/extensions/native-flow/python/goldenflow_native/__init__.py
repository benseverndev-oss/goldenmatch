"""goldenflow-native — optional Rust/PyO3 acceleration kernels for goldenflow.

This package ships ONLY the compiled abi3 ``_native`` extension. You don't
import it directly; ``goldenflow`` discovers it through
``goldenflow.core._native_loader`` when present and falls back to its pure-
Python paths when it isn't. Mirrors the polars / polars-runtime split and
goldenmatch-native: the frontend (``goldenflow``) stays a pure-Python wheel,
the compiled runtime ships separately and is pulled in via
``pip install goldenflow[native]``.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldenflow_native/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldenflow_native.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://docs.bensevern.dev/docs/goldenflow -- full docs.
  * ``goldenflow/llms.txt`` -- the host package this wheel serves, same idiom.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""

from . import _native as _native  # the compiled abi3 extension module

__all__ = ["_native"]

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("goldenflow-native")
except PackageNotFoundError:  # source checkout without installed dist metadata
    __version__ = "0.28.0"
