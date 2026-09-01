"""Modules reachable through a runtime registry.

Liveness here is COMPUTED, not inferred from references. The registries are
resolved and everything they can dispatch to is live by construction, because a
static reference scan cannot see dynamic dispatch and this codebase has 1,089
`getattr(` sites.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

from dead_code.static import REQUIRED_PACKAGES

REPO = Path(__file__).resolve().parent.parent.parent

# liveness.py sits in scripts/dead_code/, so scripts/ (where check_dead_code.py
# lives) is the parent's parent -- same sys.path pattern static.py already uses
# to reach check_dead_code, and report.py uses to reach coverage_paths.
sys.path.insert(0, str(Path(__file__).parent.parent))

from check_dead_code import _ENTRY_HUBS  # noqa: E402

_CONNECTOR_BASE = (
    REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "connectors" / "base.py"
)


def _transform_modules() -> set[str]:
    """Modules defining a transform in goldenflow's registry (113 entries)."""
    from goldenflow.transforms import list_transforms

    out: set[str] = set()
    for info in list_transforms():
        fn = getattr(info, "func", None)
        mod = getattr(fn, "__module__", None)
        if mod:
            out.add(mod)
    return out


def _cli_modules() -> set[str]:
    """Modules backing a registered typer command (36 commands).

    Walks the command tree the same way scripts/sweep_cli_polars_free.py does,
    so the two agree on what "registered" means.
    """
    import click
    import typer
    from goldenmatch.cli.main import app

    grp = typer.main.get_command(app)
    ctx = click.Context(grp)
    out: set[str] = set()

    def walk(g) -> None:
        for name in sorted(g.commands):
            cmd = g.get_command(ctx, name)
            if isinstance(cmd, click.Group):
                walk(cmd)
                continue
            mod = getattr(getattr(cmd, "callback", None), "__module__", None)
            if mod:
                out.add(mod)

    walk(grp)
    return out


def _mcp_modules() -> set[str]:
    """The MCP surface.

    Deliberately COARSE: `dispatch(name, args)` routes internally, so a tool
    name does not resolve to a handler module from outside. Marking the whole
    `goldenmatch.mcp` package live is the safe direction of error -- it can
    hide dead code inside that package, but it cannot delete a live tool. The
    precise version belongs to phase A2, which does symbol-level work.
    """
    import goldenmatch.mcp.server  # noqa: F401  -- import proves it loads

    out: set[str] = set()
    pkg = REPO / "packages" / "python" / "goldenmatch" / "goldenmatch" / "mcp"
    for f in pkg.rglob("*.py"):
        rel = f.relative_to(pkg.parent).with_suffix("")
        out.add("goldenmatch." + ".".join(rel.parts))
    return out


def _entry_point_modules() -> set[str]:
    """Modules named by a console script in any package pyproject."""
    out: set[str] = set()
    for pyproject in (REPO / "packages" / "python").glob("*/pyproject.toml"):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {}) or {}
        for target in scripts.values():
            out.add(target.split(":", 1)[0])
    return out


def _entry_hub_modules() -> set[str]:
    """Every entry-hub module name, across all six packages this detector analyses.

    check_dead_code.py's own `_ENTRY_HUBS` (imported, not copied, so there is
    one definition) lists the surfaces invoked from OUTSIDE the import graph --
    console_scripts, server bootstraps, plugin discovery. A hub module that
    nothing else in-repo imports (it is launched externally: an MCP/A2A/REST
    server bootstrap, a package `__main__`, ...) would otherwise be a permanent
    false positive for the "no importer" static signal, exactly the way a
    package `__init__` would be if package roots weren't already excluded
    there.
    """
    out: set[str] = set()
    for pkg in REQUIRED_PACKAGES:
        for hub in _ENTRY_HUBS:
            out.add(hub.format(pkg=pkg))
    return out


def _connector_registry_modules() -> set[str]:
    """Modules `load_connector()`'s `_BUILTIN` dict can dispatch to.

    packages/python/goldenmatch/goldenmatch/connectors/base.py's `load_connector()`
    resolves a connector name to a `"module:Class"` string and imports it by
    name -- no static import, so invisible to reachability (the spec's
    "Registry-aware liveness": dynamic reachability is COMPUTED, not guessed).

    Resolved via an AST scan of the dict literal rather than importing the
    module and reading the dict off it, for two reasons: `_BUILTIN` is a LOCAL
    variable inside `load_connector()`, not a module attribute, so there is
    nothing to `getattr` after import; and several of its targets (snowflake,
    databricks, ...) pull in optional third-party SDKs this detector's own
    environment has no reason to have installed. The dict literal alone names
    every module the dispatch string can resolve to, without importing any of
    them.
    """
    tree = ast.parse(_CONNECTOR_BASE.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
            continue
        if [getattr(t, "id", None) for t in node.targets] != ["_BUILTIN"]:
            continue
        for value in node.value.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out.add(value.value.split(":", 1)[0])
    return out


def live_modules() -> set[str]:
    """Union of every registry-reachable module."""
    live: set[str] = set()
    for resolve in (
        _transform_modules,
        _cli_modules,
        _mcp_modules,
        _entry_point_modules,
        _entry_hub_modules,
        _connector_registry_modules,
    ):
        live |= resolve()
    return live
