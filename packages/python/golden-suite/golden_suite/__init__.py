"""Golden Suite meta-package.

One-line, perf-optimized install and a single canonical entry point for the whole
suite. Ships almost no logic of its own — just introspection helpers and a
``golden-suite`` CLI (``doctor`` / ``optimize``). For real work, import the
individual tools (or, most of the time, just ``goldenpipe`` — the orchestrator
that adapts every other tool as a stage).

    import goldenpipe as gp
    result = gp.run("customers.csv")   # runs check -> transform -> match end to end

Native acceleration is installed by default. Use :func:`installed` to see which
components resolved, and :func:`native_status` to see whether each native kernel
is actually active (the truth behind "am I on the fast path").
"""

from __future__ import annotations

import importlib
import os
from importlib import metadata

__version__ = "0.3.4"

# PyPI distribution name -> import module name. Keep in lockstep with pyproject deps.
_COMPONENTS: dict[str, str] = {
    "goldenpipe": "goldenpipe",
    "goldenmatch": "goldenmatch",
    "goldencheck": "goldencheck",
    "goldenflow": "goldenflow",
    "infermap": "infermap",  # GoldenSchema
    "goldenanalysis": "goldenanalysis",
    "goldencheck-types": "goldencheck_types",
    "goldensuite-mcp": "goldensuite_mcp",
}

# Packages that ship an optional native (Rust/abi3) accelerator, and the pieces
# needed to reason about it WITHOUT importing the heavy top-level package:
#   base package -> (native distribution, standalone native import module, env var)
# The runtime loader tries ``<pkg>._native`` (in-tree build) then
# ``<pkg>_native._native`` (the published wheel). For a pip user only the wheel
# path exists, so probing the standalone ``<pkg>_native`` module is both accurate
# and lightweight (it does not pull in polars et al.).
_NATIVE: dict[str, tuple[str, str, str]] = {
    "goldenmatch": ("goldenmatch-native", "goldenmatch_native", "GOLDENMATCH_NATIVE"),
    "goldencheck": ("goldencheck-native", "goldencheck_native", "GOLDENCHECK_NATIVE"),
    "goldenflow": ("goldenflow-native", "goldenflow_native", "GOLDENFLOW_NATIVE"),
    "goldenanalysis": (
        "goldenanalysis-native",
        "goldenanalysis_native",
        "GOLDENANALYSIS_NATIVE",
    ),
}


def _version_or_none(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def installed() -> dict[str, str | None]:
    """Return ``{distribution_name: version-or-None}`` for every suite component.

    ``None`` means the component is not installed in this environment. The fastest
    way to confirm an integration actually got the intended setup.
    """
    return {dist: _version_or_none(dist) for dist in _COMPONENTS}


def _native_importable(native_module: str) -> bool:
    """Whether the standalone native wheel (e.g. ``goldenmatch_native._native``)
    imports cleanly — the same path the runtime loader uses under a pip install."""
    try:
        mod = importlib.import_module(native_module)
    except Exception:  # noqa: BLE001 - any import/load failure => not available
        return False
    if getattr(mod, "_native", None) is not None:
        return True
    try:  # some builds expose ``_native`` only as a submodule
        importlib.import_module(f"{native_module}._native")
        return True
    except Exception:  # noqa: BLE001
        return False


def native_status() -> dict[str, dict[str, object]]:
    """Per-package native-acceleration status.

    For each accel-capable package returns a dict with:
      - ``base_installed``  : the base package version, or ``None``
      - ``native_version``  : the native wheel version, or ``None``
      - ``native_active``   : whether the native kernel imports (fast path live)
      - ``env_mode``        : the ``<PKG>_NATIVE`` env value (``auto`` if unset)
      - ``silently_slow``   : base installed but native NOT active AND env != "0"
                              — i.e. the runtime is silently on the pure-Python path
    """
    out: dict[str, dict[str, object]] = {}
    for pkg, (native_dist, native_module, env_var) in _NATIVE.items():
        base_installed = _version_or_none(pkg)
        native_version = _version_or_none(native_dist)
        native_active = _native_importable(native_module)
        env_mode = os.environ.get(env_var, "auto").lower()
        silently_slow = (
            base_installed is not None and not native_active and env_mode != "0"
        )
        out[pkg] = {
            "base_installed": base_installed,
            "native_dist": native_dist,
            "native_version": native_version,
            "native_active": native_active,
            "env_var": env_var,
            "env_mode": env_mode,
            "silently_slow": silently_slow,
        }
    return out


__all__ = ["__version__", "installed", "native_status"]
