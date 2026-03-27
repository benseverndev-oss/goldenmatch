"""CLI entry points for GoldenCheck."""
from __future__ import annotations
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
import polars as pl
import typer
from typer.core import TyperGroup
from goldencheck.engine.scanner import scan_file, scan_file_with_llm
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.validator import validate_file
from goldencheck.config.loader import load_config
from goldencheck.config.writer import save_config
from goldencheck.reporters.rich_console import report_rich
from goldencheck.reporters.json_reporter import report_json
from goldencheck.reporters.ci_reporter import report_ci

from goldencheck import __version__


@contextmanager
def _cli_error_handler():
    """Catch common errors and print friendly messages instead of tracebacks."""
    try:
        yield
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except PermissionError as e:
        typer.echo(f"Error: Permission denied: {e}", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except pl.exceptions.ComputeError as e:
        typer.echo(f"Error: Could not parse file. {e}", err=True)
        raise typer.Exit(code=1)


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
    help="Data validation that discovers rules from your data so you don't have to write them.\n\nExit codes: 0 = pass, 1 = findings at/above --fail-on, 2 = usage error.",
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

    files: list[Path] = []
    no_tui = False
    json_output = False
    llm_boost = False
    llm_provider = "anthropic"
    domain = None
    smart = False
    guided = False
    no_history = False
    webhook = None
    notify_on = "grade-drop"
    html = None
    while args:
        arg = args.pop(0)
        if arg == "--no-tui":
            no_tui = True
        elif arg == "--json":
            json_output = True
        elif arg == "--llm-boost":
            llm_boost = True
        elif arg == "--llm-provider":
            if not args:
                typer.echo("Error: '--llm-provider' requires a value.", err=True)
                raise typer.Exit(code=2)
            llm_provider = args.pop(0)
        elif arg == "--domain":
            if not args:
                typer.echo("Error: '--domain' requires a value.", err=True)
                raise typer.Exit(code=2)
            domain = args.pop(0)
        elif arg == "--smart":
            smart = True
        elif arg == "--guided":
            guided = True
        elif arg == "--no-history":
            no_history = True
        elif arg == "--webhook":
            if not args:
                typer.echo("Error: '--webhook' requires a value.", err=True)
                raise typer.Exit(code=2)
            webhook = args.pop(0)
        elif arg == "--notify-on":
            if not args:
                typer.echo("Error: '--notify-on' requires a value.", err=True)
                raise typer.Exit(code=2)
            notify_on = args.pop(0)
        elif arg == "--html":
            if not args:
                typer.echo("Error: '--html' requires a value.", err=True)
                raise typer.Exit(code=2)
            html = Path(args.pop(0))
        elif not arg.startswith("-"):
            files.append(Path(arg))
        else:
            typer.echo(f"Error: Unknown option '{arg}'.", err=True)
            raise typer.Exit(code=2)

    if not files:
        typer.echo("Error: Missing data file argument.", err=True)
        raise typer.Exit(code=1)

    for file in files:
        _do_scan(
            file, no_tui=no_tui, json_output=json_output, llm_boost=llm_boost,
            llm_provider=llm_provider, domain=domain, smart=smart, guided=guided,
            no_history=no_history, webhook=webhook, notify_on=notify_on, html=html,
        )


@app.command()
def scan(
    files: list[Path] = typer.Argument(..., help="Data file(s) to profile."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
    llm_boost: bool = typer.Option(False, "--llm-boost", help="Enable LLM enhancement pass."),
    llm_provider: str = typer.Option("anthropic", "--llm-provider", help="LLM provider: anthropic or openai."),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack: healthcare, finance, ecommerce."),
    smart: bool = typer.Option(False, "--smart", help="Auto-triage: pin high-confidence, dismiss low."),
    guided: bool = typer.Option(False, "--guided", help="Walk through findings one at a time."),
    no_history: bool = typer.Option(False, "--no-history", help="Don't record this scan in history."),
    webhook: Optional[str] = typer.Option(None, "--webhook", help="URL to POST findings to."),
    notify_on: str = typer.Option("grade-drop", "--notify-on", help="Trigger: grade-drop, any-error, any-warning."),
    html: Optional[Path] = typer.Option(None, "--html", help="Generate HTML report at this path."),
) -> None:
    """Profile one or more data files and report findings."""
    if smart and guided:
        typer.echo("Error: Cannot use --smart and --guided together.", err=True)
        raise typer.Exit(code=2)
    for file in files:
        _do_scan(
            file, no_tui=no_tui, json_output=json_output, llm_boost=llm_boost,
            llm_provider=llm_provider, domain=domain, smart=smart, guided=guided,
            no_history=no_history, webhook=webhook, notify_on=notify_on, html=html,
        )


@app.command()
def validate(
    file: Path = typer.Argument(..., help="Data file to validate."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to goldencheck.yml."),
    no_tui: bool = typer.Option(False, "--no-tui", help="Disable TUI and print Rich output instead."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Validate a data file against pinned rules in goldencheck.yml."""
    with _cli_error_handler():
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
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack: healthcare, finance, ecommerce."),
) -> None:
    """Profile AND validate a file, launching TUI for interactive review."""
    with _cli_error_handler():
        if llm_boost:
            findings, profile = scan_file_with_llm(file, provider=llm_provider, domain=domain)
        else:
            findings, profile = scan_file(file)
            findings = apply_confidence_downgrade(findings, llm_boost=False)
        config_path = config or Path("goldencheck.yml")
        cfg = load_config(config_path)
        if cfg is not None:
            val_findings = validate_file(file, cfg)
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
    with _cli_error_handler():
        from goldencheck.llm.rule_generator import generate_rules, save_rules
        from goldencheck.engine.reader import read_file
        from goldencheck.engine.sampler import maybe_sample

        df = read_file(file)
        sample = maybe_sample(df, max_rows=100_000)

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


@app.command()
def fix(
    file: Path = typer.Argument(..., help="Data file to fix."),
    mode: str = typer.Option("safe", "--mode", "-m", help="Fix mode: safe, moderate, or aggressive."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show fixes without writing."),
    force: bool = typer.Option(False, "--force", help="Required for aggressive mode."),
) -> None:
    """Apply automated fixes to a data file.

    Modes:
      safe       — trim whitespace, remove invisible chars, normalize Unicode (default)
      moderate   — safe + standardize enum case, fix smart quotes
      aggressive — moderate + coerce types (requires --force)
    """
    with _cli_error_handler():
        from goldencheck.engine.fixer import apply_fixes
        from goldencheck.engine.reader import read_file
        from goldencheck.engine.scanner import scan_file as _scan

        df = read_file(file)
        findings, _ = _scan(file)

        try:
            fixed_df, report = apply_fixes(df, findings, mode=mode, force=force)
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

        if not report.entries:
            typer.echo("No issues found — file is clean.")
            raise typer.Exit(code=0)

        typer.echo(f"\nFixes applied ({mode} mode):")
        for entry in report.entries:
            typer.echo(f"  {entry.column}: {entry.fix_type} ({entry.rows_affected} rows)")
        typer.echo(f"\nTotal: {report.total_rows_fixed} row-fixes across {len(report.entries)} operations")

        if dry_run:
            typer.echo("\n--dry-run: No file written.")
            raise typer.Exit(code=0)

        out_path = output or Path(f"{file.stem}_fixed{file.suffix}")
        if out_path.resolve() == Path(file).resolve():
            typer.echo("Error: Output path is the same as input. Use -o to specify a different path.", err=True)
            raise typer.Exit(code=1)

        ext = file.suffix.lower()
        if ext == ".parquet":
            fixed_df.write_parquet(out_path)
        elif ext in (".xlsx", ".xls"):
            csv_out = out_path.with_suffix(".csv")
            fixed_df.write_csv(csv_out)
            typer.echo("Note: Excel input converted to CSV output (single sheet)")
            out_path = csv_out
        else:
            fixed_df.write_csv(out_path)

        typer.echo(f"Written to: {out_path}")


@app.command()
def diff(
    file: Path = typer.Argument(..., help="Data file to compare."),
    file2: Optional[Path] = typer.Argument(None, help="Second file (omit to compare against git)."),
    ref: Optional[str] = typer.Option(None, "--ref", help="Git ref to compare against (default: HEAD)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Compare two versions of a data file.

    Shows schema changes, finding changes, and statistics delta.

    Auto-detects git: if the file is tracked, compares against HEAD.
    Pass a second file for explicit two-file comparison.
    """
    import json
    import subprocess
    import tempfile
    from goldencheck.engine.differ import diff_files, format_diff_report
    from goldencheck.engine.reader import read_file

    with _cli_error_handler():
        if file2:
            # Two-file mode: file=old, file2=new
            old_df = read_file(file)
            old_findings, old_profile = scan_file(file)
            old_findings = apply_confidence_downgrade(old_findings, llm_boost=False)
            new_df = read_file(file2)
            new_findings, new_profile = scan_file(file2)
            new_findings = apply_confidence_downgrade(new_findings, llm_boost=False)
            label = f"{file.name} vs {file2.name}"
        else:
            # Git mode
            git_ref = ref or "HEAD"
            try:
                result = subprocess.run(
                    ["git", "show", f"{git_ref}:{file}"],
                    capture_output=True, check=True,
                )
            except subprocess.CalledProcessError:
                typer.echo(
                    f"Error: Cannot read '{file}' from git ref '{git_ref}'. "
                    "Provide a second file for explicit comparison.",
                    err=True,
                )
                raise typer.Exit(code=1)

            with tempfile.NamedTemporaryFile(suffix=file.suffix, delete=False, mode="wb") as tmp:
                tmp.write(result.stdout)
                tmp_path = Path(tmp.name)

            try:
                old_df = read_file(tmp_path)
                old_findings, old_profile = scan_file(tmp_path)
                old_findings = apply_confidence_downgrade(old_findings, llm_boost=False)
            finally:
                tmp_path.unlink(missing_ok=True)

            new_df = read_file(file)
            new_findings, new_profile = scan_file(file)
            new_findings = apply_confidence_downgrade(new_findings, llm_boost=False)
            label = f"{file.name} (current vs {git_ref})"

        report = diff_files(old_df, new_df, old_findings, new_findings, old_profile, new_profile)

        if json_output:
            from dataclasses import asdict
            typer.echo(json.dumps(asdict(report), indent=2, default=str))
        else:
            typer.echo(format_diff_report(report, label))


@app.command()
def watch(
    directory: Path = typer.Argument(..., help="Directory to watch."),
    interval: int = typer.Option(60, "--interval", "-i", help="Poll interval in seconds."),
    pattern: Optional[str] = typer.Option(None, "--pattern", "-p", help="Glob pattern (e.g., '*.csv')."),
    exit_on: Optional[str] = typer.Option(None, "--exit-on", help="Exit on severity: error or warning."),
    json_output: bool = typer.Option(False, "--json", help="JSON output per scan."),
) -> None:
    """Watch a directory for data file changes and re-scan.

    Polls for file modifications and re-scans changed files.
    Use --exit-on for CI pipelines that should fail on first error.
    """
    from goldencheck.engine.watcher import watch_directory

    with _cli_error_handler():
        exit_code = watch_directory(
            directory,
            interval=interval,
            pattern=pattern,
            exit_on=exit_on,
            json_output=json_output,
        )
        raise typer.Exit(code=exit_code)


@app.command()
def init(
    file: Path = typer.Argument(..., help="Data file to scan for initial rules."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept defaults, skip interactive prompts."),
) -> None:
    """Interactive setup wizard — scan, pin rules, scaffold CI.

    Scans your data, auto-pins high-confidence findings, and generates
    goldencheck.yml + CI workflow in one command.
    """
    from goldencheck.cli.init_wizard import run_init_wizard

    with _cli_error_handler():
        run_init_wizard(file, yes=yes)


@app.command()
def history(
    file: Optional[Path] = typer.Argument(None, help="Filter history by file."),
    last: Optional[int] = typer.Option(None, "--last", "-n", help="Show last N scans."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show scan history — scores, grades, and trends over time."""
    import json as json_mod
    from goldencheck.engine.history import load_history

    file_filter = file.name if file else None
    records = load_history(file_filter=file_filter, last_n=last)

    if not records:
        typer.echo("No scan history found. Run a scan first.")
        raise typer.Exit(code=0)

    if json_output:
        from dataclasses import asdict
        typer.echo(json_mod.dumps([asdict(r) for r in records], indent=2))
        raise typer.Exit(code=0)

    # Table output
    typer.echo(f"{'Date':<20} {'File':<20} {'Score':>5} {'Grade':>5} {'Errors':>6} {'Warnings':>8}")
    for r in records:
        ts = r.timestamp[:16].replace("T", " ")
        typer.echo(f"{ts:<20} {r.file:<20} {r.score:>5} {r.grade:>5} {r.errors:>6} {r.warnings:>8}")

    if len(records) >= 2:
        first, last_r = records[0], records[-1]
        if first.file == last_r.file:
            typer.echo(f"\nTrend: {first.file} {first.score} -> {last_r.score}")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to."),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on."),
) -> None:
    """Start the GoldenCheck REST API server.

    Endpoints: POST /scan (file upload), POST /scan/url (scan from URL),
    GET /health, GET /checks, GET /domains.
    """
    from goldencheck.server import run_server as _run_http
    _run_http(host=host, port=port)


@app.command(name="scan-db")
def scan_db(
    connection: str = typer.Argument(..., help="Database connection string (postgres://, snowflake://, etc.)"),
    table: Optional[str] = typer.Option(None, "--table", "-t", help="Table name to scan."),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Custom SQL query."),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    html: Optional[Path] = typer.Option(None, "--html", help="Generate HTML report."),
    sample_size: int = typer.Option(100000, "--sample-size", help="Max rows to fetch."),
) -> None:
    """Scan a database table directly.

    Supports Postgres, Snowflake, BigQuery, and any SQLAlchemy-compatible database.
    Requires: pip install connectorx (or sqlalchemy + pandas)
    """
    with _cli_error_handler():
        from goldencheck.engine.db_scanner import scan_database
        findings, profile = scan_database(
            connection, table=table, query=query,
            sample_size=sample_size, domain=domain,
        )

        if html:
            from goldencheck.reporters.html_reporter import report_html
            report_html(findings, profile, html)
            typer.echo(f"HTML report: {html}", err=True)

        if json_output:
            report_json(findings, profile, sys.stdout)
        else:
            report_rich(findings, profile)


@app.command()
def schedule(
    files: list[Path] = typer.Argument(..., help="Data files to scan on schedule."),
    interval: str = typer.Option("daily", "--interval", "-i", help="Interval: hourly, daily, weekly, 5min, 15min, 30min, or seconds."),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack."),
    webhook: Optional[str] = typer.Option(None, "--webhook", help="Webhook URL for notifications."),
    notify_on: str = typer.Option("grade-drop", "--notify-on", help="Trigger: grade-drop, any-error, any-warning."),
    json_output: bool = typer.Option(False, "--json", help="JSON output per scan."),
) -> None:
    """Run scans on a schedule (hourly, daily, weekly, or custom interval).

    Like 'watch' but time-based instead of file-change-based.
    """
    with _cli_error_handler():
        from goldencheck.engine.scheduler import run_schedule
        run_schedule(
            files, interval=interval, domain=domain,
            webhook=webhook, notify_on=notify_on, json_output=json_output,
        )


@app.command(name="mcp-serve")
def mcp_serve(
    transport: str = typer.Option("stdio", help="Transport: 'stdio' or 'http'"),
    host: str = typer.Option("0.0.0.0", help="Host for HTTP transport"),
    port: int = typer.Option(8100, help="Port for HTTP transport"),
) -> None:
    """Start the MCP server for Claude Desktop (stdio) or remote deployment (http)."""
    if transport == "http":
        try:
            from goldencheck.mcp.server import run_server_http
        except ImportError:
            typer.echo(
                "Error: MCP dependencies not installed. Run: pip install goldencheck[mcp]",
                err=True,
            )
            raise typer.Exit(code=1)

        run_server_http(host=host, port=port)
    else:
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


@app.command(name="agent-serve")
def agent_serve(
    port: int = typer.Option(8100, help="Port for the A2A server"),
) -> None:
    """Start the A2A agent server for agent-to-agent communication."""
    try:
        from goldencheck.a2a.server import run_a2a_server
    except ImportError:
        typer.echo(
            "Error: Agent dependencies not installed. Run: pip install goldencheck[agent]",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Starting GoldenCheck A2A agent on port {port}...")
    typer.echo(f"Agent card: http://localhost:{port}/.well-known/agent.json")
    import asyncio
    asyncio.run(run_a2a_server(port=port))


@app.command()
def evaluate(
    file: Path = typer.Argument(..., help="Data file to scan."),
    ground_truth: Path = typer.Option(
        ..., "--ground-truth", "-g", help="JSON file with expected findings.",
    ),
    min_f1: float = typer.Option(0.0, "--min-f1", help="Minimum F1 score; exit 1 if below."),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON."),
) -> None:
    """Evaluate scan accuracy against a ground-truth JSON file.

    The ground-truth file should be a JSON array of objects with "column" and "check" keys.
    Prints precision, recall, F1, and detail breakdowns. Exits 1 if F1 < --min-f1.
    """
    import json as json_mod
    from goldencheck.engine.evaluate import evaluate_scan

    with _cli_error_handler():
        if not ground_truth.exists():
            typer.echo(f"Error: Ground truth file not found: {ground_truth}", err=True)
            raise typer.Exit(code=1)

        findings, profile = scan_file(file)
        findings = apply_confidence_downgrade(findings, llm_boost=False)

        expected = json_mod.loads(ground_truth.read_text(encoding="utf-8"))
        result = evaluate_scan(findings, expected)

        if json_output:
            # Serialize tuple lists as lists of lists for JSON compat
            out = {
                k: [list(t) for t in v] if k.endswith("_details") else v
                for k, v in result.items()
            }
            typer.echo(json_mod.dumps(out, indent=2))
        else:
            typer.echo(f"Precision:       {result['precision']:.4f}")
            typer.echo(f"Recall:          {result['recall']:.4f}")
            typer.echo(f"F1:              {result['f1']:.4f}")
            typer.echo(f"True positives:  {result['true_positives']}")
            typer.echo(f"False positives: {result['false_positives']}")
            typer.echo(f"False negatives: {result['false_negatives']}")

            if result["fp_details"]:
                typer.echo("\nFalse positives:")
                for col, chk in result["fp_details"]:
                    typer.echo(f"  {col}: {chk}")

            if result["fn_details"]:
                typer.echo("\nFalse negatives (missed):")
                for col, chk in result["fn_details"]:
                    typer.echo(f"  {col}: {chk}")

        if result["f1"] < min_f1:
            typer.echo(
                f"\nF1 {result['f1']:.4f} is below minimum {min_f1:.4f}", err=True,
            )
            raise typer.Exit(code=1)


@app.command()
def demo(
    no_tui: bool = typer.Option(False, "--no-tui", help="Print results to stdout."),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack to apply."),
) -> None:
    """Run GoldenCheck on built-in sample data to see it in action."""
    from goldencheck.cli.demo_data import generate_demo_csv

    path = generate_demo_csv()
    typer.echo(f"Generated demo data: {path}")
    typer.echo("Scanning for data quality issues...\n")
    _do_scan(path, no_tui=True, json_output=False, domain=domain)


def _do_scan(
    file: Path,
    *,
    no_tui: bool,
    json_output: bool,
    llm_boost: bool = False,
    llm_provider: str = "anthropic",
    domain: str | None = None,
    smart: bool = False,
    guided: bool = False,
    no_history: bool = False,
    webhook: str | None = None,
    notify_on: str = "grade-drop",
    html: Path | None = None,
) -> None:
    """Run scan and output results."""
    with _cli_error_handler():
        if llm_boost:
            findings, profile = scan_file_with_llm(file, provider=llm_provider, domain=domain)
        else:
            findings, profile = scan_file(file, domain=domain)
            findings = apply_confidence_downgrade(findings, llm_boost=False)

        # Progress message (to stderr so it doesn't pollute --json)
        sample_note = ""
        if profile.row_count > 100_000:
            sample_note = ", sampled to 100,000"
        typer.echo(
            f"Scanned {profile.row_count:,} rows, {profile.column_count} columns{sample_note}",
            err=True,
        )

        # Record history (before triage, so raw findings are recorded)
        if not no_history:
            from goldencheck.engine.history import record_scan
            record_scan(file, profile, findings)

        # Smart auto-triage
        if smart:
            from goldencheck.engine.triage import auto_triage
            triage = auto_triage(findings)
            typer.echo(f"Auto-triaged {len(findings)} findings:")
            typer.echo(f"  Pinned:    {len(triage.pin)} (high confidence)")
            typer.echo(f"  Dismissed: {len(triage.dismiss)} (low confidence or INFO)")
            typer.echo(f"  Review:    {len(triage.review)} (medium — use --guided)")

            if triage.pin:
                from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, Settings
                config = GoldenCheckConfig(settings=Settings(fail_on="error"))
                for f in triage.pin:
                    if f.column not in config.columns:
                        config.columns[f.column] = ColumnRule(type="string")
                save_config(config, Path("goldencheck.yml"))
                typer.echo(f"\nWritten to goldencheck.yml ({len(config.columns)} rules)")
            return

        # Guided walkthrough
        if guided:
            from goldencheck.engine.triage import auto_triage
            from goldencheck.models.finding import Severity
            triage = auto_triage(findings)
            reviewable = [f for f in findings if f.severity >= Severity.WARNING]
            if not reviewable:
                typer.echo("No findings to review.")
                return

            pinned = []
            for i, f in enumerate(reviewable, 1):
                conf = "HIGH" if f.confidence >= 0.8 else "MED" if f.confidence >= 0.5 else "LOW"
                samples = ", ".join(f.sample_values[:3]) if f.sample_values else ""
                typer.echo(f"\n[{i}/{len(reviewable)}] {f.severity.name}: '{f.column}' — {f.message[:80]}")
                typer.echo(f"      Confidence: {conf}  |  Samples: {samples}")
                choice = typer.prompt("      Pin this rule? [Y/n/skip]", default="y")
                if choice.lower() in ("y", "yes", ""):
                    pinned.append(f)

            if pinned:
                from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, Settings
                config = GoldenCheckConfig(settings=Settings(fail_on="error"))
                for f in pinned:
                    if f.column not in config.columns:
                        config.columns[f.column] = ColumnRule(type="string")
                save_config(config, Path("goldencheck.yml"))
                typer.echo(f"\nPinned {len(pinned)} rules -> goldencheck.yml")
            else:
                typer.echo("\nNo rules pinned.")
            return

        # HTML report (generate alongside normal output)
        if html:
            from goldencheck.reporters.html_reporter import report_html
            report_html(findings, profile, html)
            typer.echo(f"HTML report: {html}", err=True)

        # Normal output
        if json_output:
            report_json(findings, profile, sys.stdout)
        elif not no_tui:
            from goldencheck.tui.app import GoldenCheckApp
            tui_app = GoldenCheckApp(findings=findings, profile=profile)
            tui_app.run()
        else:
            report_rich(findings, profile)

        # Webhook notification
        if webhook:
            from goldencheck.engine.history import get_previous_scan
            from goldencheck.engine.notifier import should_notify, send_webhook

            from goldencheck.models.finding import Severity as _Sev
            by_col: dict[str, dict[str, int]] = {}
            for f in findings:
                if f.severity >= _Sev.WARNING:
                    by_col.setdefault(f.column, {"errors": 0, "warnings": 0})
                    key = "errors" if f.severity == _Sev.ERROR else "warnings"
                    by_col[f.column][key] = by_col[f.column].get(key, 0) + 1
            grade, score = profile.health_score(findings_by_column=by_col)

            prev = get_previous_scan(file)
            if should_notify(grade, findings, prev, notify_on):
                send_webhook(
                    webhook, str(file), grade, score, findings,
                    trigger=notify_on,
                    previous_grade=prev.grade if prev else None,
                )
