"""goldenprofile-native -- PyO3 binding for the goldenprofile Virtual
Fingerprint engine.

Re-exports the compiled ``resolve_json`` (str -> str over a JSON boundary). The
host pipeline (``goldengraph.profile``) builds the request and parses the
response; this package is just the marshaling surface. See
``goldenprofile_core::ResolveRequest`` for the JSON schema.
"""

from __future__ import annotations

from ._native import __version__, resolve_json

__all__ = ["resolve_json", "__version__"]
