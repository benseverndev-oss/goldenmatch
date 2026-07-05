#!/usr/bin/env python3
"""Advisory: does the PUBLISHED goldenmatch-native wheel still export every native
symbol the current host source depends on? Warns (does not gate) on republish lag —
the #688 class. Reuses Project 1's host-reference scanner.
Run (needs the published wheel installed): python scripts/check_native_wheel.py goldenmatch"""
from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys

# Reuse Project 1's scanner (it's a script, not a package) by path-import.
_p1 = importlib.util.spec_from_file_location(
    "check_native_symbols", pathlib.Path(__file__).parent / "check_native_symbols.py")
_ns = importlib.util.module_from_spec(_p1); sys.modules[_p1.name] = _ns
_p1.loader.exec_module(_ns)

# Which installed wheel module to introspect, per package.
_WHEEL_MODULE = {"goldenmatch": "goldenmatch_native._native"}


def _public_callables(module) -> set[str]:
    """Python-visible callable exports (the runtime-registered names). Keeps classes
    like ExcludeSet; drops dunders/private and non-callables."""
    return {n for n in dir(module)
            if not n.startswith("_") and callable(getattr(module, n))}


def wheel_exports(module_name: str) -> set[str]:
    return _public_callables(importlib.import_module(module_name))


def lag(referenced: set[str], shipped: set[str], allow: set[str]) -> set[str]:
    return referenced - shipped - allow


def run(package: str, module_name: str | None = None) -> int:
    spec = _ns.REGISTRY.get(package)
    if spec is None:
        sys.stderr.write(f"no registry entry for '{package}'\n")
        return 2
    referenced = _ns.scan_references(spec["py_root"], spec["loader_tokens"])
    if not referenced:  # falsely-green guard (mirrors check_native_symbols)
        sys.stderr.write(f"FAIL: scanned zero host references for {package} — "
                         f"the reference idiom is wrong\n")
        return 2
    module_name = module_name or _WHEEL_MODULE.get(package)
    if module_name is None:
        sys.stderr.write(f"no wheel module known for '{package}'\n")
        return 2
    try:
        shipped = wheel_exports(module_name)
    except Exception as e:  # noqa: BLE001 - can't introspect => fail loud, never falsely green
        sys.stderr.write(f"FAIL: could not import/introspect published wheel "
                         f"'{module_name}': {e!r}\n")
        return 2
    lagging = lag(referenced, shipped, _ns.load_allow(spec["allow"]))
    if lagging:
        print(f"::warning::published {module_name} lags current source — "
              f"republish goldenmatch-native")
        for s in sorted(lagging):
            print(f"::warning::  host references '{s}' but the published wheel "
                  f"does not export it")
        return 0  # ADVISORY: warn, do not fail the job
    print(f"{package}: published wheel exports all {len(referenced)} "
          f"host-referenced symbols — up to date")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("usage: check_native_wheel.py <package> [module_name]")
    raise SystemExit(run(argv[0], argv[1] if len(argv) > 1 else None))
