#!/usr/bin/env python3
"""Emit goldenmatch's real Python operation surface as JSON: {package, mcp_tools, cli_commands}.
Runtime introspection of the actual registries. Needs the surface-bearing extras installed
(goldenmatch[mcp]); a missing extra exits 3 (environment gap), distinct from a code breakage (2)."""
from __future__ import annotations
import json, sys

# Per-package registry map. Each surface -> a callable returning a list[str] of names.
# Extend this dict to add packages (the follow-up); goldenmatch is the reference.
def _goldenmatch_mcp() -> list[str]:
    from goldenmatch.mcp.server import TOOLS      # needs goldenmatch[mcp]
    return [t.name for t in TOOLS]

def _goldenmatch_cli() -> list[str]:
    # NOTE: deliberately uses typer.main.get_command(app).commands.keys() rather than the spec's
    # §3.1 app.registered_commands/registered_groups. get_command resolves the real CLI names
    # (hyphenation like `mcp-serve`, and commands whose .name is None derive from the function
    # name) and includes groups — the authoritative surface a user actually types. Verified.
    from typer.main import get_command
    from goldenmatch.cli.main import app
    names = list(get_command(app).commands.keys())  # resolved leaves + groups (mcp-serve, pprl, ...)
    if len(names) != len(set(names)):
        raise SystemExit("CLI leaf/group name collision in goldenmatch — surface is ambiguous")
    return names

REGISTRY = {
    "goldenmatch": {
        "mcp_tools": (_goldenmatch_mcp, "mcp"),      # (emitter, extra-name for the env-gap message)
        "cli_commands": (_goldenmatch_cli, None),
    },
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
