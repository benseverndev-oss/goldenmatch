"""``golden-suite`` CLI: verify and repair the perf-optimized setup.

    golden-suite doctor      # report every component + whether native is actually active
    golden-suite optimize    # install any missing native kernels, then re-verify

``doctor`` is read-only and exits non-zero when the runtime would silently run the
slow pure-Python path — so it doubles as a CI/verification gate. ``optimize`` acts.
"""

from __future__ import annotations

import json as _json
import subprocess
import sys

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, installed, native_status

app = typer.Typer(
    add_completion=False,
    help="Verify and repair the perf-optimized Golden Suite setup.",
    no_args_is_help=True,
)
_console = Console()
_err = Console(stderr=True)


def _components_table() -> Table:
    table = Table(title="Golden Suite components", title_style="bold")
    table.add_column("Package")
    table.add_column("Version")
    for dist, version in installed().items():
        if version is None:
            table.add_row(dist, "[yellow]not installed[/]")
        else:
            table.add_row(dist, version)
    return table


def _native_table(status: dict[str, dict[str, object]]) -> Table:
    table = Table(title="Native acceleration", title_style="bold")
    table.add_column("Package")
    table.add_column("Native wheel")
    table.add_column("Fast path")
    table.add_column("Env")
    table.add_column("Verdict")
    for pkg, s in status.items():
        if s["base_installed"] is None:
            continue  # base package not installed; nothing to accelerate
        wheel = s["native_version"] or "[yellow]missing[/]"
        if s["native_active"]:
            fast = "[green]native[/]"
        else:
            fast = "[red]pure-python[/]"
        if s["env_mode"] == "0":
            verdict = "[yellow]native disabled (env=0)[/]"
        elif s["silently_slow"]:
            verdict = "[red]SILENTLY SLOW[/]"
        elif s["native_active"]:
            verdict = "[green]OK[/]"
        else:
            verdict = "[yellow]inactive[/]"
        table.add_row(pkg, str(wheel), fast, str(s["env_mode"]), verdict)
    return table


@app.command()
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    strict: bool = typer.Option(
        True,
        "--strict/--no-strict",
        help="Exit non-zero if any package is silently on the pure-Python path.",
    ),
) -> None:
    """Report every component and whether the native fast path is actually active."""
    status = native_status()
    silently_slow = [p for p, s in status.items() if s["silently_slow"]]
    missing_base = [d for d, v in installed().items() if v is None]

    if as_json:
        _console.print_json(
            _json.dumps(
                {
                    "version": __version__,
                    "installed": installed(),
                    "native": status,
                    "silently_slow": silently_slow,
                    "missing_components": missing_base,
                }
            )
        )
    else:
        _console.print(_components_table())
        _console.print(_native_table(status))
        if silently_slow:
            _err.print(
                f"\n[red]FAIL[/]: {', '.join(silently_slow)} installed but running "
                f"pure-Python. Run [bold]golden-suite optimize[/] to fix."
            )
        else:
            _console.print("\n[green]OK[/]: native acceleration active where expected.")

    if strict and silently_slow:
        raise typer.Exit(code=1)


@app.command()
def optimize(
    strict_runtime: bool = typer.Option(
        False,
        "--strict/--no-strict",
        help=(
            "Also emit require-native env vars (<PKG>_NATIVE=1). WARNING: strict "
            "mode forces native for components NOT yet parity-signed-off (notably "
            "goldenflow) and can change outputs. Off by default."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be installed, do nothing."
    ),
) -> None:
    """Install any missing native kernels for this platform, then re-verify."""
    status = native_status()
    # A package needs repair when its base is installed but the native wheel is
    # absent or won't import on this interpreter.
    to_install = [
        str(s["native_dist"])
        for s in status.values()
        if s["base_installed"] is not None and not s["native_active"]
    ]

    if not to_install:
        _console.print("[green]Already optimal[/]: every native kernel is active.")
    else:
        _console.print(
            f"Native kernels to install: [bold]{', '.join(to_install)}[/]"
        )
        if dry_run:
            _console.print("[yellow]--dry-run[/]: nothing installed.")
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *to_install]
            _console.print(f"$ {' '.join(cmd)}")
            result = subprocess.run(cmd, check=False)
            if result.returncode != 0:
                _err.print(
                    "[red]pip install failed[/]. On a platform without a published "
                    "wheel, native cannot be enabled — see the docs for supported "
                    "platforms."
                )
                raise typer.Exit(code=result.returncode)

    if strict_runtime:
        _console.print(
            "\n[bold]Require-native env[/] (add to your shell/.env to make a missing "
            "kernel raise instead of silently falling back):"
        )
        for s in status.values():
            if s["base_installed"] is not None:
                _console.print(f"  export {s['env_var']}=1")
        _err.print(
            "\n[yellow]WARNING[/]: <PKG>_NATIVE=1 forces native for components not "
            "yet parity-signed-off (e.g. goldenflow) and MAY change outputs. Only "
            "use it if you have validated parity for your workload."
        )

    if not dry_run:
        _console.print()
        doctor(as_json=False, strict=False)


@app.command()
def version() -> None:
    """Print the golden-suite meta-package version."""
    _console.print(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
