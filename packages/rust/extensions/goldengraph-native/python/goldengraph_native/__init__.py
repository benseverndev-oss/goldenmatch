"""goldengraph-native -- PyO3 binding for the goldengraph-core knowledge-graph engine.

Ships the compiled abi3 ``_native`` extension: build a resolution-merged entity
graph from extracted mentions + relationships, then query 1-2 hop neighborhoods.
The compute is pure Rust (``goldengraph-core``); this wheel is the thin Python
surface. Mirrors goldenmatch's native / goldenmatch-native split.
"""

from . import _native as _native  # the compiled abi3 extension module

__all__ = ["_native"]

# Read the version from the installed distribution metadata (maturin sets it
# from pyproject `[project].version`) so it can never drift from the wheel.
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("goldengraph-native")
except PackageNotFoundError:  # source checkout without installed dist metadata
    __version__ = "0.1.0"
