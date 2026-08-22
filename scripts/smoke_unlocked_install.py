#!/usr/bin/env python3
"""Smoke the entry points that a fresh, UNLOCKED resolution would break.

Run inside an environment built by `pip install` with no lockfile and no
constraints -- i.e. what `Dockerfile.mcp` does and what every user gets. CI's
normal `uv sync` resolves through uv.lock, which made it structurally blind to
mcp 2.0.0: the lock pinned 1.28.1, the image resolved 2.0.0, and the difference
only showed up as a crashed production deploy.

Deliberately not a pytest suite. It has to run against an env that has ONLY the
declared dependencies -- no dev group, no fakesnow, no pytest -- because that is
the env being tested. Adding pytest to it would change the resolution it exists
to check.

Each check targets a place a major bump has actually landed or would land
silently: an import chain, an object model we read into, and a console script.
"""
from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version

FAILURES: list[str] = []


def check(label: str):
    def deco(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- report every failure, not the first
            FAILURES.append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok    {label}")
        return fn
    return deco


print("resolved versions:")
for dist in ("goldenmatch", "mcp", "pyarrow", "numpy", "pydantic", "typer", "duckdb"):
    try:
        print(f"  {dist:<12} {version(dist)}")
    except Exception:
        print(f"  {dist:<12} (absent)")
print()
print("checks:")


@check("import goldenmatch")
def _import():
    import goldenmatch  # noqa: F401


@check("MCP server module imports")
def _mcp_import():
    import goldenmatch.mcp.server  # noqa: F401


@check("MCP tool list builds (reads Tool.inputSchema -- the mcp 2.0 break)")
def _mcp_tools():
    import goldenmatch.mcp.server as s
    aliases = s._build_alias_tools()
    assert aliases, "alias tool list is empty"
    schema = aliases[0].inputSchema
    assert isinstance(schema, dict) and schema, f"unusable inputSchema: {schema!r}"


@check("console script responds")
def _cli():
    out = subprocess.run(
        [sys.executable, "-m", "goldenmatch.cli.main", "--version"],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"exit {out.returncode}: {out.stderr.strip()[:300]}")


print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) failed under an unlocked resolution.")
    print("An upstream major has changed something we depend on. Either adapt to")
    print("it, or add a ceiling for THAT dependency with a comment saying why --")
    print("do not cap everything (see scripts/audit_dep_ceilings.py).")
    sys.exit(1)
print("all checks passed under an unlocked resolution")
