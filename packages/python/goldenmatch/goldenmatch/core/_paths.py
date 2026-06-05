"""Path validation (CodeQL py/path-injection mitigation).

GoldenMatch is local-first: reading the user's own files by path is the
product, so containment is OPT-IN. Setting GOLDENMATCH_ALLOWED_ROOT (or
passing base_dir) jails all user-supplied paths under that root --
deploy-time hardening for network-exposed surfaces (the Railway MCP
server sets it to the /data volume).

An empty string for base_dir or GOLDENMATCH_ALLOWED_ROOT is treated as
unset, meaning containment checking is disabled. Symlink TOCTOU
(resolve-then-open race) is an accepted non-goal: the threat model is
attacker-supplied path STRINGS, not attacker control of the server's
filesystem state.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_ROOT = "GOLDENMATCH_ALLOWED_ROOT"


class PathOutsideAllowedRootError(ValueError):
    """Raised when a user-supplied path escapes the configured root."""


def safe_path(value: str | os.PathLike, *, base_dir: str | os.PathLike | None = None) -> Path:
    """Normalize *value* and enforce containment within a configured root.

    Raises ValueError on NUL bytes, PathOutsideAllowedRootError on missing root
    configuration or containment escape.
    """
    raw = os.fspath(value)
    if "\x00" in raw:
        raise ValueError("path contains NUL byte")
    resolved = Path(raw).resolve()
    root = base_dir if base_dir is not None else os.environ.get(_ENV_ROOT)
    if not root:
        raise PathOutsideAllowedRootError(
            f"allowed root is not configured; set {_ENV_ROOT} or pass base_dir"
        )
    root_resolved = Path(root).resolve()
    if not resolved.is_relative_to(root_resolved):
        raise PathOutsideAllowedRootError(
            f"path {str(resolved)!r} is outside allowed root {str(root_resolved)!r}"
        )
    return resolved
