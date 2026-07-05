#!/usr/bin/env python3
"""Emit a package's real Python operation surface as JSON: {package, mcp_tools, cli_commands}.
Runtime introspection of the actual registries. Needs the surface-bearing extras installed
(<pkg>[mcp]); a missing extra exits 3 (environment gap), distinct from a code breakage (2).

The surface is UNIFORM across the suite: MCP tools = `<pkg>.mcp.server.TOOLS` (each a Tool with
.name); CLI = the Typer app resolved via typer.main.get_command(app).commands.keys() — which
gives the real names a user types (hyphenation like `mcp-serve`, and sub-app group names),
unlike registered_commands whose .name can be None. Only the CLI module path differs per package.
"""
from __future__ import annotations
import importlib
import json
import sys


def _tool_name(t) -> str:
    # Tool registries vary: mcp-SDK Tool objects expose `.name`; packages with a custom
    # JSON-RPC MCP server (e.g. goldenflow) list plain dicts with a "name" key.
    return t.name if hasattr(t, "name") else t["name"]


def _mcp(package: str):
    def fn() -> list[str]:
        mod = importlib.import_module(f"{package}.mcp.server")  # needs <pkg>[mcp]
        return [_tool_name(t) for t in mod.TOOLS]
    return fn


def _cli(cli_module: str):
    def fn() -> list[str]:
        from typer.main import get_command
        app = importlib.import_module(cli_module).app
        names = list(get_command(app).commands.keys())
        if len(names) != len(set(names)):
            raise SystemExit(f"CLI leaf/group name collision in {cli_module} — surface is ambiguous")
        return names
    return fn


# The only per-package variance on the Python side is the CLI module path.
_CLI_MODULE = {
    "goldenmatch": "goldenmatch.cli.main",
    "goldencheck": "goldencheck.cli.main",
    "goldenflow": "goldenflow.cli.main",
    "goldenpipe": "goldenpipe.cli.main",
    "goldenanalysis": "goldenanalysis.cli.main",
    "infermap": "infermap.cli",
}

# Each surface -> (callable returning list[str], extra-name for the env-gap message).
REGISTRY = {
    pkg: {"mcp_tools": (_mcp(pkg), "mcp"), "cli_commands": (_cli(mod), None)}
    for pkg, mod in _CLI_MODULE.items()
}


def emit(package: str) -> dict:
    spec = REGISTRY.get(package)
    if spec is None:
        raise SystemExit(f"no parity registry entry for '{package}'")
    out = {"package": package}
    for surface, (fn, extra) in spec.items():
        try:
            out[surface] = sorted(fn())
        except ModuleNotFoundError as e:
            # a surface-bearing OPTIONAL extra is absent -> environment gap, not drift
            sys.stderr.write(f"environment not provisioned for {package}.{surface}: "
                             f"install {package}[{extra}] (missing module: {e.name})\n")
            raise SystemExit(3)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: emit_python_surface.py <package>")
    print(json.dumps(emit(sys.argv[1]), sort_keys=True))
