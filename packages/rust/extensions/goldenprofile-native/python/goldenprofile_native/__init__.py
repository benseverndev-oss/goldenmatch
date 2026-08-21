"""goldenprofile-native -- PyO3 binding for the goldenprofile Virtual
Fingerprint engine.

Re-exports the compiled ``resolve_json`` (str -> str over a JSON boundary). The
host pipeline (``goldengraph.profile``) builds the request and parses the
response; this package is just the marshaling surface. See
``goldenprofile_core::ResolveRequest`` for the JSON schema.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from the
compiled extension, or from the host package's fallback path):

  * ``goldenprofile_native/llms.txt`` -- ships INSIDE this wheel, next to this file:
    ``Path(goldenprofile_native.__file__).parent / "llms.txt"``. Condensed, current,
    written for machine readers.
  * https://github.com/benseverndev-oss/goldenmatch/tree/main/packages/rust/extensions/goldenprofile-core -- full docs.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: this directory is a compiled artefact. Which surface
owns a computation, which fallback is deliberate, and which parity is
contract-tested are all *decisions* -- documented, and not recoverable by
reading the binary or the Python fallback beside it.
"""

from __future__ import annotations

from ._native import __version__, resolve_json

__all__ = ["resolve_json", "__version__"]
