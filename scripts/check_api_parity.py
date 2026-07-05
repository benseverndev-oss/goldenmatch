#!/usr/bin/env python3
"""Cross-language API parity gate. See docs/superpowers/specs/2026-07-04-api-parity-gate-design.md.

check_partition/check_structure are pure (dicts + sets); the CLI layer adds YAML + descriptor I/O.
"""
from __future__ import annotations

from typing import NamedTuple

SURFACES = ("mcp_tools", "cli_commands")


class ParityFailure(NamedTuple):
    surface: str
    name: str
    kind: str
    message: str


def check_partition(surface: str, manifest_surface: dict, py: set[str], ts: set[str]) -> list[ParityFailure]:
    """Assert the manifest exactly partitions py|ts. Returns [] when clean."""
    shared = set(manifest_surface.get("shared", []))
    py_only = set(manifest_surface.get("python_only", []))
    ts_only = set(manifest_surface.get("ts_only", []))
    declared = shared | py_only | ts_only
    both, only_py, only_ts = py & ts, py - ts, ts - py
    f: list[ParityFailure] = []

    def add(name, kind, msg):
        f.append(ParityFailure(surface, name, kind, msg))

    for n in sorted(both - shared):                       # row 1
        add(n, "unshared_common", f"'{n}' exists in both -> add to {surface}.shared")
    for n in sorted(only_py - py_only - shared):          # row 2
        add(n, "undeclared_py_only", f"'{n}' is Python-only and undeclared -> add to {surface}.python_only or port it to TS")
    for n in sorted(only_ts - ts_only - shared):          # row 3
        add(n, "undeclared_ts_only", f"'{n}' is TS-only and undeclared -> add to {surface}.ts_only or add it to Python")
    for n in sorted((shared & (py | ts)) - py):           # row 4a (shared, present in TS, gone from Python; absent-from-both is a phantom, row 7)
        add(n, "shared_missing_py", f"'{n}' is declared shared but missing from Python")
    for n in sorted((shared & (py | ts)) - ts):           # row 4b (shared, present in Python, gone from TS)
        add(n, "shared_missing_ts", f"'{n}' is declared shared but missing from TS")
    for n in sorted(py_only & ts):                        # row 5
        add(n, "py_only_in_ts", f"'{n}' is marked python_only but now exists in TS -> move to {surface}.shared")
    for n in sorted(ts_only & py):                        # row 6
        add(n, "ts_only_in_py", f"'{n}' is marked ts_only but now exists in Python -> move to {surface}.shared")
    for n in sorted(declared - (py | ts)):                # row 7
        add(n, "phantom", f"'{n}' is in the manifest but no longer exists -> remove it")
    return f
