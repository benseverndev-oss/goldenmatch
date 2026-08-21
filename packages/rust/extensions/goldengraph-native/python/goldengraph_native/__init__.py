"""goldengraph-native -- PyO3 binding for the goldengraph-core knowledge-graph engine.

Ships the compiled abi3 ``_native`` extension: build a resolution-merged entity
graph from extracted mentions + relationships, then query 1-2 hop neighborhoods.
The compute is pure Rust (``goldengraph-core``); this wheel is the thin Python
surface. Mirrors goldenmatch's native / goldenmatch-native split.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldengraph_native/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldengraph_native.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/python/goldengraph -- full docs.
  * ``goldengraph/llms.txt`` -- the host package this wheel serves, same idiom.
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
    __version__ = _pkg_version("goldengraph-native")
except PackageNotFoundError:  # source checkout without installed dist metadata
    __version__ = "0.2.0"
