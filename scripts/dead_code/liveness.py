"""Modules reachable through a runtime registry.

Liveness here is COMPUTED, not inferred from references. The registries are
resolved and everything they can dispatch to is live by construction, because a
static reference scan cannot see dynamic dispatch and this codebase has 1,089
`getattr(` sites.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _transform_modules() -> set[str]:
    """Modules defining a transform in goldenflow's registry (147 entries)."""
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


def live_modules() -> set[str]:
    """Union of every registry-reachable module."""
    live: set[str] = set()
    for resolve in (
        _transform_modules,
        _cli_modules,
        _mcp_modules,
        _entry_point_modules,
    ):
        live |= resolve()
    return live
