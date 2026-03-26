from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="goldenflow",
    help="GoldenFlow: data transformation toolkit.",
    invoke_without_command=True,
)


def _version_callback(value: bool):
    if value:
        from goldenflow import __version__
        typer.echo(f"goldenflow {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback, is_eager=True),
):
    if ctx.invoked_subcommand is None and ctx.args:
        # Auto-route to transform if first arg looks like a file or stdin sentinel
        first_arg = ctx.args[0]
        if first_arg == "-" or Path(first_arg).exists():
            ctx.invoke(transform, path=Path(first_arg))


@app.command()
def transform(
    path: Path = typer.Argument(..., help="Input data file"),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="YAML config file"),
    output_dir: Optional[Path] = typer.Option(None, "-o", "--output-dir", help="Output directory"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack to use"),
    from_findings: bool = typer.Option(False, "--from-findings", help="Read findings from stdin"),
    llm_mode: bool = typer.Option(False, "--llm", help="Enable LLM-enhanced transforms"),
    strict: bool = typer.Option(False, "--strict", help="Fail if any transform errors occur"),
):
    """Transform a data file (zero-config or config-driven)."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        import sys
        import json
        import io
        import os

        import polars as pl

        if llm_mode:
            import goldenflow.llm.corrector  # noqa: F401 — registers the transform
            os.environ["GOLDENFLOW_LLM"] = "1"

        import goldenflow
        from goldenflow.config.loader import load_config
        from goldenflow.config.schema import TransformSpec
        from goldenflow.engine.selector import select_from_findings
        from goldenflow.engine.transformer import TransformEngine
        from goldenflow.reporters.rich_console import print_manifest

        cfg = load_config(config) if config else goldenflow.GoldenFlowConfig()

        if domain:
            from goldenflow.domains import load_domain
            domain_pack = load_domain(domain)
            if domain_pack:
                cfg = domain_pack.default_config

        # Handle --from-findings: read JSON findings from stdin and build config
        if from_findings:
            stdin_data = sys.stdin.read()
            findings = json.loads(stdin_data) if stdin_data.strip() else []
            if isinstance(findings, dict):
                findings = findings.get("findings", [findings])
            col_transforms = select_from_findings(findings)
            cfg.transforms = [
                TransformSpec(column=col, ops=ops)
                for col, ops in col_transforms.items()
            ]

        # Handle stdin input (path == "-")
        is_stdin = str(path) == "-"
        if is_stdin:
            stdin_bytes = sys.stdin.buffer.read()
            df = pl.read_csv(io.BytesIO(stdin_bytes))
            engine = TransformEngine(config=cfg)
            result = engine.transform_df(df)
            if output_dir is None:
                # Write to stdout
                result.df.write_csv(sys.stdout)
                return
            else:
                from goldenflow.connectors.file import write_file
                output_dir.mkdir(parents=True, exist_ok=True)
                write_file(result.df, output_dir / "transformed.csv")
                result.manifest.save(output_dir / "manifest.json")
                print_manifest(result.manifest)
                return

        engine = TransformEngine(config=cfg)

        if output_dir is None:
            output_dir = path.parent

        result = engine.transform_file(path, output_dir=output_dir)
        print_manifest(result.manifest)
        typer.echo(f"\nOutput: {output_dir / (path.stem + '_transformed' + path.suffix)}")

        if strict and result.manifest.errors:
            from goldenflow.reporters.rich_console import console
            console.print(f"[red]Strict mode: {len(result.manifest.errors)} transform errors[/red]")
            for e in result.manifest.errors[:5]:
                console.print(f"  {e.column}/{e.transform}: {e.error}")
            raise typer.Exit(1)


@app.command()
def validate(
    path: Path = typer.Argument(..., help="Input data file"),
    config: Optional[Path] = typer.Option(None, "-c", "--config"),
):
    """Dry-run: show what would change without writing."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        import goldenflow
        from goldenflow.config.loader import load_config
        from goldenflow.engine.transformer import TransformEngine
        from goldenflow.reporters.rich_console import print_manifest

        cfg = load_config(config) if config else goldenflow.GoldenFlowConfig()
        engine = TransformEngine(config=cfg)
        result = engine.transform_file(path)
        typer.echo("Dry run — would change:")
        print_manifest(result.manifest)


@app.command()
def profile(
    path: Path = typer.Argument(..., help="Input data file"),
):
    """Show column profiles for a data file."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        from goldenflow.connectors.file import read_file
        from goldenflow.engine.profiler_bridge import profile_dataframe
        from goldenflow.reporters.rich_console import print_profile

        df = read_file(path)
        prof = profile_dataframe(df, file_path=str(path))
        print_profile(prof)


@app.command()
def learn(
    path: Path = typer.Argument(..., help="Input data file"),
    output: Path = typer.Option("goldenflow.yaml", "-o", "--output", help="Output config path"),
):
    """Generate a YAML config from data patterns."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        from goldenflow.config.learner import learn_config
        from goldenflow.config.loader import save_config

        cfg = learn_config(path)
        save_config(cfg, output)
        typer.echo(f"Config saved to {output}")


@app.command()
def diff(
    before: Path = typer.Argument(..., help="Before file"),
    after: Path = typer.Argument(..., help="After file"),
):
    """Compare pre/post transform files."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        from goldenflow.connectors.file import read_file
        from goldenflow.engine.differ import diff_dataframes
        from goldenflow.reporters.rich_console import print_diff

        df_before = read_file(before)
        df_after = read_file(after)
        result = diff_dataframes(df_before, df_after)
        print_diff(result)


@app.command(name="map")
def map_cmd(
    source: Path = typer.Option(..., "--source", "-s", help="Source data file"),
    target: Path = typer.Option(..., "--target", "-t", help="Target data file or schema"),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Mapping config"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Save mapping config"),
):
    """Auto-map schemas between source and target."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        from goldenflow.connectors.file import read_file
        from goldenflow.mapping.schema_mapper import SchemaMapper
        from goldenflow.reporters.rich_console import console

        source_df = read_file(source)
        target_df = read_file(target)
        mapper = SchemaMapper()
        mappings = mapper.map(source_df, target_df)

        for m in mappings:
            tier = "auto" if m.confidence >= 0.9 else "suggest" if m.confidence >= 0.6 else "skip"
            console.print(f"  {m.source} → {m.target} ({m.confidence:.2f}) [{tier}]")

        if output:
            from goldenflow.config.loader import save_config
            cfg = mapper.to_config(mappings)
            save_config(cfg, output)
            console.print(f"\nMapping saved to {output}")


@app.command()
def interactive(
    path: Optional[Path] = typer.Argument(None, help="Input data file"),
):
    """Launch the interactive TUI."""
    from goldenflow.tui.app import GoldenFlowApp
    app_tui = GoldenFlowApp(path=path)
    app_tui.run()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
):
    """Launch the REST API server."""
    import uvicorn
    from goldenflow.api.server import create_app
    uvicorn.run(create_app(), host=host, port=port)


@app.command(name="mcp-serve")
def mcp_serve():
    """Start MCP server for Claude Desktop."""
    from goldenflow.mcp.server import run_server
    run_server()


@app.command(name="agent-serve")
def agent_serve(
    port: int = typer.Option(8150, "--port"),
):
    """Start the A2A agent server."""
    try:
        from goldenflow.a2a.server import run_server
        run_server(port=port)
    except ImportError:
        from rich.console import Console
        Console().print("[red]aiohttp not installed. Run: pip install goldenflow[agent][/red]")
        raise typer.Exit(code=1)


@app.command()
def watch(
    path: Path = typer.Argument(".", help="Directory to watch"),
    config: Optional[Path] = typer.Option(None, "-c", "--config"),
    output_dir: Optional[Path] = typer.Option(None, "-o", "--output-dir"),
    interval: float = typer.Option(2.0, "--interval", help="Poll interval in seconds"),
):
    """Watch a directory and auto-transform new/changed files."""
    from goldenflow.cli.watch import watch_directory
    watch_directory(path, config_path=config, output_dir=output_dir, interval=interval)


@app.command()
def schedule(
    path: Path = typer.Argument(..., help="Data file to transform"),
    interval: str = typer.Option("1h", "--every", help="Interval (e.g., 5m, 1h, 30s)"),
    config: Optional[Path] = typer.Option(None, "-c", "--config"),
    output_dir: Optional[Path] = typer.Option(None, "-o", "--output-dir"),
):
    """Run transforms on a schedule."""
    from goldenflow.cli.schedule import run_schedule
    run_schedule(path, interval=interval, config_path=config, output_dir=output_dir)


@app.command(name="init")
def init_cmd(
    data: Optional[Path] = typer.Argument(None, help="Data file to profile"),
    output: Path = typer.Option("goldenflow.yaml", "-o", "--output"),
):
    """Interactive setup wizard to generate a config file."""
    from goldenflow.cli.init_wizard import run_wizard
    run_wizard(data_path=data, output_path=output)


@app.command()
def history(
    limit: int = typer.Option(20, "-n", "--limit", help="Number of recent runs to show"),
):
    """Show recent transform runs."""
    from goldenflow.history import list_runs
    from goldenflow.reporters.rich_console import console
    from rich.table import Table

    runs = list_runs(limit=limit)
    if not runs:
        console.print("[dim]No transform history yet.[/dim]")
        return

    table = Table(title="Transform History")
    table.add_column("Run ID", style="cyan")
    table.add_column("Source", style="dim")
    table.add_column("Rows", style="green")
    table.add_column("Transforms", style="yellow")
    table.add_column("Errors", style="red")
    table.add_column("Time", style="dim")

    for r in runs:
        table.add_row(r.run_id, Path(r.source).name, str(r.rows), str(r.transforms_applied), str(r.errors), r.timestamp[:19])

    console.print(table)


@app.command()
def demo(
    output_dir: Path = typer.Option(Path("."), "-o", "--output-dir"),
):
    """Generate sample data and config for trying GoldenFlow."""
    from goldenflow.cli.errors import cli_error_handler

    with cli_error_handler():
        import polars as pl

        # Generate messy sample data
        df = pl.DataFrame({
            "name": ["  John Smith  ", "DR. JANE DOE", "mcdonald, robert", "Mary O'Brien"],
            "email": ["JOHN@EXAMPLE.COM", "  jane@test.com  ", "bob@test.com", "mary@sample.com"],
            "phone": ["(555) 123-4567", "555.987.6543", "+1-555-456-7890", "5554567890"],
            "state": ["Pennsylvania", "CA", "new york", "IL"],
            "signup_date": ["03/15/2024", "2024-01-20", "Jan 5, 2023", "12/25/2022"],
            "price": ["$1,234.56", "$99.99", "$0.50", "$5,000.00"],
            "status": ["active", "ACTIVE", "actve", "inactive"],
        })

        data_path = output_dir / "demo_data.csv"
        df.write_csv(data_path)

        # Generate sample config
        config_content = '''# GoldenFlow Demo Config
transforms:
  - column: name
    ops: [strip, title_case]
  - column: email
    ops: [lowercase, strip]
  - column: phone
    ops: [phone_e164]
  - column: state
    ops: [state_abbreviate]
  - column: signup_date
    ops: [date_iso8601]
  - column: price
    ops: [currency_strip]
'''
        config_path = output_dir / "demo_config.yaml"
        config_path.write_text(config_content)

        from goldenflow.reporters.rich_console import console
        console.print("[green]Demo files created:[/green]")
        console.print(f"  Data:   {data_path}")
        console.print(f"  Config: {config_path}")
        console.print("\n[bold]Try these commands:[/bold]")
        console.print(f"  goldenflow transform {data_path}")
        console.print(f"  goldenflow transform {data_path} -c {config_path}")
        console.print(f"  goldenflow profile {data_path}")
        console.print(f"  goldenflow learn {data_path}")


@app.command()
def stream(
    path: Path = typer.Argument(..., help="Input data file"),
    chunk_size: int = typer.Option(10000, "--chunk-size", help="Rows per batch"),
    config: Optional[Path] = typer.Option(None, "-c", "--config"),
    output_dir: Optional[Path] = typer.Option(None, "-o", "--output-dir"),
):
    """Stream-process a large file in chunks."""
    import polars as pl

    from goldenflow.config.loader import load_config
    from goldenflow.config.schema import GoldenFlowConfig
    from goldenflow.streaming import StreamProcessor
    from goldenflow.reporters.rich_console import console
    from rich.progress import Progress

    cfg = load_config(config) if config else GoldenFlowConfig()
    processor = StreamProcessor(config=cfg)
    out = output_dir or path.parent

    results = []
    with Progress() as progress:
        task = progress.add_task("Streaming...", total=None)
        for result in processor.stream_file(path, chunk_size=chunk_size):
            results.append(result.df)
            progress.update(task, advance=chunk_size, description=f"Batch {processor.batches_processed}")

    if results:
        combined = pl.concat(results)
        from goldenflow.connectors.file import write_file
        out_path = out / f"{path.stem}_transformed{path.suffix}"
        write_file(combined, out_path)
        console.print(f"[green]Streamed {processor.batches_processed} batches, {combined.shape[0]:,} rows total[/green]")
        console.print(f"Output: {out_path}")
