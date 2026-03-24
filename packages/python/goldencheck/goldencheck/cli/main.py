"""CLI entry points for GoldenCheck."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional
import typer
from typer.core import TyperGroup
from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.validator import validate_file
from goldencheck.config.loader import load_config
from goldencheck.reporters.rich_console import report_rich
from goldencheck.reporters.json_reporter import report_json
from goldencheck.reporters.ci_reporter import report_ci

__version__ = "0.3.0"


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
    llm_boost = False
    llm_provider = "anthropic"
    while args:
        arg = args.pop(0)
        if arg == "--no-tui":
            no_tui = True
        elif arg == "--json":
            json_output = True
        elif arg == "--llm-boost":
            llm_boost = True
        elif arg == "--llm-provider":
            llm_provider = args.pop(0)
        elif not arg.startswith("-"):
            if file is None:
                file = Path(arg)
        else:
            typer.echo(f"Error: Unknown option '{arg}'.", err=True)
            raise typer.Exit(code=2)

    if file is None:
        typer.echo("Error: Missing data file argument.", err=True)
        raise typer.Exit(code=1)

    _do_scan(file, no_tui=no_tui, json_output=json_output, llm_boost=llm_boost, llm_provider=llm_provider)


@app.command()
def scan(
    file: Path = typer.Argument(..., help="Data file to profile."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
    llm_boost: bool = typer.Option(False, "--llm-boost", help="Enable LLM enhancement pass."),
    llm_provider: str = typer.Option("anthropic", "--llm-provider", help="LLM provider: anthropic or openai."),
) -> None:
    """Profile a data file and report findings."""
    _do_scan(file, no_tui=no_tui, json_output=json_output, llm_boost=llm_boost, llm_provider=llm_provider)


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
    elif not no_tui:
        from goldencheck.tui.app import GoldenCheckApp
        tui_app = GoldenCheckApp(findings=findings, profile=profile, config=cfg)
        tui_app.run()
    else:
        report_rich(findings, profile)

    exit_code = report_ci(findings, cfg.settings.fail_on)
    raise typer.Exit(code=exit_code)


@app.command()
def review(
    file: Path = typer.Argument(..., help="Data file to profile and validate."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to goldencheck.yml."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
    llm_boost: bool = typer.Option(False, "--llm-boost", help="Enable LLM enhancement pass."),
    llm_provider: str = typer.Option("anthropic", "--llm-provider", help="LLM provider: anthropic or openai."),
) -> None:
    """Profile AND validate a file, launching TUI for interactive review."""
    if llm_boost:
        findings, profile = scan_file_with_llm(file, provider=llm_provider)
    else:
        findings, profile = scan_file(file)
        findings = apply_confidence_downgrade(findings, llm_boost=False)
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
    elif not no_tui:
        from goldencheck.tui.app import GoldenCheckApp
        tui_app = GoldenCheckApp(findings=findings, profile=profile, config=cfg)
        tui_app.run()
    else:
        report_rich(findings, profile)

    exit_code = report_ci(findings, fail_on)
    raise typer.Exit(code=exit_code)


@app.command()
def learn(
    file: Path = typer.Argument(..., help="Data file to analyze."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output path for rules (default: goldencheck_rules.json)."),
    llm_provider: str = typer.Option("anthropic", "--llm-provider", help="LLM provider: anthropic or openai."),
) -> None:
    """Generate validation rules using LLM analysis of your data.

    Sends a representative sample to an LLM which generates domain-specific
    validation rules (regex, length, value lists, cross-column checks).
    Rules are saved and automatically applied on future scans.
    """
    from goldencheck.llm.rule_generator import generate_rules, save_rules
    from goldencheck.engine.reader import read_file
    from goldencheck.engine.sampler import maybe_sample

    df = read_file(file)
    sample = maybe_sample(df, max_rows=100_000)

    # Run profilers first to give LLM context
    findings, _ = scan_file(file)

    typer.echo(f"Analyzing {len(df)} rows, {len(df.columns)} columns...")
    rules = generate_rules(sample, findings, provider=llm_provider)

    if not rules:
        typer.echo("No rules generated.", err=True)
        raise typer.Exit(code=1)

    out_path = output or Path("goldencheck_rules.json")
    save_rules(rules, out_path)
    typer.echo(f"Generated {len(rules)} rules → {out_path}")

    for r in rules:
        typer.echo(f"  [{r.rule_type}] {r.column}: {r.description}")


@app.command(name="mcp-serve")
def mcp_serve() -> None:
    """Start the MCP server (stdio) for Claude Desktop integration."""
    try:
        from goldencheck.mcp.server import run_server
    except ImportError:
        typer.echo(
            "Error: MCP dependencies not installed. Run: pip install goldencheck[mcp]",
            err=True,
        )
        raise typer.Exit(code=1)

    import asyncio
    asyncio.run(run_server())


def _do_scan(
    file: Path,
    *,
    no_tui: bool,
    json_output: bool,
    llm_boost: bool = False,
    llm_provider: str = "anthropic",
) -> None:
    """Run scan and output results."""
    if llm_boost:
        findings, profile = scan_file_with_llm(file, provider=llm_provider)
    else:
        findings, profile = scan_file(file)
        findings = apply_confidence_downgrade(findings, llm_boost=False)

    if json_output:
        report_json(findings, profile, sys.stdout)
    elif not no_tui:
        from goldencheck.tui.app import GoldenCheckApp
        tui_app = GoldenCheckApp(findings=findings, profile=profile)
        tui_app.run()
    else:
        report_rich(findings, profile)
