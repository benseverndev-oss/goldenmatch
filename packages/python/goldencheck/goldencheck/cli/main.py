"""CLI entry points for GoldenCheck."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional
import typer
from typer.core import TyperGroup
from goldencheck.engine.scanner import scan_file
from goldencheck.engine.validator import validate_file
from goldencheck.config.loader import load_config
from goldencheck.reporters.rich_console import report_rich
from goldencheck.reporters.json_reporter import report_json
from goldencheck.reporters.ci_reporter import report_ci

__version__ = "0.1.0"


class _DefaultCommandGroup(TyperGroup):
    """TyperGroup that falls back to invoke_without_command when the first
    positional argument is not a registered subcommand name.

    This enables the ``goldencheck data.csv`` shorthand that aliases to scan.
    """

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:  # type: ignore[override]
        result = super().parse_args(ctx, args)
        # If _protected_args holds something that is not a known subcommand,
        # treat the whole remainder as extra args for the callback.
        if ctx._protected_args:
            first = ctx._protected_args[0]
            if first not in self.commands and not first.startswith("-"):
                ctx.args = ctx._protected_args + ctx.args
                ctx._protected_args = []
        return result


app = typer.Typer(
    name="goldencheck",
    help="Data validation that discovers rules from your data so you don't have to write them.",
    add_completion=False,
    cls=_DefaultCommandGroup,
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"GoldenCheck {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """GoldenCheck — profile and validate data files.

    Pass a data file directly to run 'scan' (default):

        goldencheck data.csv [--no-tui] [--json]
    """
    if ctx.invoked_subcommand is not None:
        return

    # Parse leftover args from the default alias behaviour
    args = list(ctx.args)
    if not args:
        typer.echo(ctx.get_help())
        raise typer.Exit()

    file: Path | None = None
    no_tui = False
    json_output = False
    while args:
        arg = args.pop(0)
        if arg == "--no-tui":
            no_tui = True
        elif arg == "--json":
            json_output = True
        elif not arg.startswith("-"):
            if file is None:
                file = Path(arg)
        else:
            typer.echo(f"Error: Unknown option '{arg}'.", err=True)
            raise typer.Exit(code=2)

    if file is None:
        typer.echo("Error: Missing data file argument.", err=True)
        raise typer.Exit(code=1)

    _do_scan(file, no_tui=no_tui, json_output=json_output)


@app.command()
def scan(
    file: Path = typer.Argument(..., help="Data file to profile."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Profile a data file and report findings."""
    _do_scan(file, no_tui=no_tui, json_output=json_output)


@app.command()
def validate(
    file: Path = typer.Argument(..., help="Data file to validate."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to goldencheck.yml."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Validate a data file against pinned rules in goldencheck.yml."""
    config_path = config or Path("goldencheck.yml")
    cfg = load_config(config_path)
    if cfg is None:
        typer.echo(
            f"Error: No configuration found at '{config_path}'. "
            "Run 'goldencheck scan' first to generate a config.",
            err=True,
        )
        raise typer.Exit(code=1)

    findings = validate_file(file, cfg)
    _, profile = scan_file(file, sample_size=cfg.settings.sample_size)

    if json_output:
        report_json(findings, profile, sys.stdout)
    else:
        if not no_tui:
            typer.echo("TUI not yet implemented, showing CLI output")
        report_rich(findings, profile)

    exit_code = report_ci(findings, cfg.settings.fail_on)
    raise typer.Exit(code=exit_code)


@app.command()
def review(
    file: Path = typer.Argument(..., help="Data file to profile and validate."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to goldencheck.yml."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Profile AND validate a file, launching TUI for interactive review."""
    findings, profile = scan_file(file)
    config_path = config or Path("goldencheck.yml")
    cfg = load_config(config_path)
    if cfg is not None:
        val_findings = validate_file(file, cfg)
        # Merge: validation findings take precedence (deduplicate by column+check)
        existing = {(f.column, f.check) for f in val_findings}
        for f in findings:
            if (f.column, f.check) not in existing:
                val_findings.append(f)
        findings = val_findings
        fail_on = cfg.settings.fail_on
    else:
        fail_on = "error"

    if json_output:
        report_json(findings, profile, sys.stdout)
    else:
        if not no_tui:
            typer.echo("TUI not yet implemented, showing CLI output")
        report_rich(findings, profile)

    exit_code = report_ci(findings, fail_on)
    raise typer.Exit(code=exit_code)


def _do_scan(file: Path, *, no_tui: bool, json_output: bool) -> None:
    """Run scan and output results."""
    findings, profile = scan_file(file)

    if json_output:
        report_json(findings, profile, sys.stdout)
    else:
        if not no_tui:
            typer.echo("TUI not yet implemented, showing CLI output")
        report_rich(findings, profile)
