"""GoldenPipe CLI -- pipeline framework for data quality."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="GoldenPipe -- pluggable pipeline framework for data quality")
console = Console()


@app.command()
def run(
    source: str = typer.Argument(..., help="Input file path"),
    config: str | None = typer.Option(None, "--config", "-c", help="Pipeline YAML config"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show reasoning and timing"),
    # Identity Graph (v1.2) -- when --identity-path is set on a zero-config
    # run, the identity_resolve stage is auto-appended to the default
    # check->flow->dedupe chain with these knobs as its stage_config. When
    # the user supplies an explicit YAML config the flags are ignored and
    # the YAML wins.
    identity_path: str | None = typer.Option(
        None, "--identity-path",
        help="Enable Identity Graph; persist to this SQLite path",
    ),
    identity_dataset: str | None = typer.Option(
        None, "--identity-dataset", help="Identity Graph dataset namespace",
    ),
    identity_source_pk: str | None = typer.Option(
        None, "--identity-source-pk",
        help="Column to derive `{source}:{source_pk}` record_id from",
    ),
    identity_weak_threshold: float | None = typer.Option(
        None, "--identity-weak-threshold",
        help="Auto-flag bottleneck pair as conflicts_with when cluster "
             "confidence < this. Defaults to 0.6 (per IdentityConfig).",
    ),
) -> None:
    """Run a pipeline on a data file."""
    from goldenpipe._api import run as gp_run

    identity_opts = None
    if identity_path is not None:
        identity_opts = {"path": identity_path}
        if identity_dataset is not None:
            identity_opts["dataset"] = identity_dataset
        if identity_source_pk is not None:
            identity_opts["source_pk_column"] = identity_source_pk
        if identity_weak_threshold is not None:
            identity_opts["weak_confidence_threshold"] = identity_weak_threshold

    result = gp_run(source, config=config, identity_opts=identity_opts)

    table = Table(title=f"GoldenPipe: {result.source}")
    table.add_column("Stage", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    for name, sr in result.stages.items():
        color = {"success": "green", "skipped": "yellow", "failed": "red"}.get(
            sr.status.value, "dim"
        )
        details = sr.error or ""
        table.add_row(name, f"[{color}]{sr.status.value}[/{color}]", details)

    console.print(table)
    console.print(
        f"\n[bold]{result.status.value.upper()}[/bold] | "
        f"{result.input_rows} rows | {result.source}"
    )

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for e in result.errors:
            console.print(f"  - {e}")

    if verbose:
        if result.reasoning:
            console.print("\n[bold]Reasoning:[/bold]")
            for k, v in result.reasoning.items():
                if not k.startswith("_"):
                    console.print(f"  {k}: {v}")
        if result.timing:
            console.print("\n[bold]Timing:[/bold]")
            for k, v in result.timing.items():
                console.print(f"  {k}: {v:.2f}s")

    if output and result.artifacts.get("golden") is not None:
        result.artifacts["golden"].write_csv(output)
        console.print(f"\nGolden records written to {output}")


@app.command()
def stages() -> None:
    """List all discovered stages."""
    from goldenpipe.engine.registry import StageRegistry

    reg = StageRegistry()
    reg.discover()
    all_stages = reg.list_all()

    table = Table(title="Discovered Stages")
    table.add_column("Name", style="bold")
    table.add_column("Produces")
    table.add_column("Consumes")

    for name, info in sorted(all_stages.items()):
        table.add_row(name, ", ".join(info.produces), ", ".join(info.consumes))

    console.print(table)
    console.print(f"\n{len(all_stages)} stage(s) found")


@app.command()
def validate(
    config: str = typer.Option(..., "--config", "-c", help="Pipeline YAML config"),
) -> None:
    """Dry-run wiring validation without executing."""
    from goldenpipe.config.loader import load_config
    from goldenpipe.engine.registry import StageRegistry
    from goldenpipe.engine.resolver import Resolver, WiringError

    try:
        cfg = load_config(config)
        reg = StageRegistry()
        reg.discover()
        plan = Resolver.resolve(cfg, reg)
        console.print(f"[green]Valid[/green] -- {len(plan.stages)} stages resolved")
        for s in plan.stages:
            console.print(f"  {s.name}")
    except WiringError as e:
        console.print(f"[red]Wiring Error:[/red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)


@app.command()
def init(
    dir: str = typer.Option(".", "--dir", "-d", help="Directory to create config in"),
) -> None:
    """Generate a starter goldenpipe.yml from installed tools."""
    from goldenpipe.engine.registry import StageRegistry

    reg = StageRegistry()
    reg.discover()
    all_stages = reg.list_all()

    lines = ["pipeline: my-pipeline", "stages:"]
    for name in sorted(all_stages.keys()):
        lines.append(f"  - {name}")

    if not all_stages:
        lines.append("  # No stages discovered. Install goldenpipe[golden-suite] or add custom stages.")

    out = Path(dir) / "goldenpipe.yml"
    out.write_text("\n".join(lines) + "\n")
    console.print(f"Created {out}")


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port for REST API"),
) -> None:
    """Start the REST API server."""
    try:
        import uvicorn

        from goldenpipe.api.server import create_app
        uvicorn.run(create_app(), host="0.0.0.0", port=port)
    except ImportError:
        console.print("[red]FastAPI not installed. Run: pip install goldenpipe[api][/red]")
        raise typer.Exit(code=1)


@app.command(name="mcp-serve")
def mcp_serve(
    transport: str = typer.Option("stdio", help="Transport type: stdio or http"),
    host: str = typer.Option("0.0.0.0", help="Host for HTTP transport"),
    port: int = typer.Option(8250, help="Port for HTTP transport"),
) -> None:
    """Start the MCP server."""
    try:
        if transport == "http":
            from goldenpipe.mcp.server import run_server_http
            run_server_http(host=host, port=port)
        else:
            from goldenpipe.mcp.server import run_server
            run_server()
    except ImportError:
        console.print("[red]MCP not installed. Run: pip install goldenpipe[mcp][/red]")
        raise typer.Exit(code=1)


@app.command(name="agent-serve")
def agent_serve(
    port: int = typer.Option(8250, help="Port for A2A server"),
) -> None:
    """Start the A2A agent server."""
    try:
        from goldenpipe.a2a.server import run_server
        run_server(port=port)
    except ImportError:
        console.print("[red]aiohttp not installed. Run: pip install goldenpipe[agent][/red]")
        raise typer.Exit(code=1)


@app.command()
def interactive() -> None:
    """Launch the TUI."""
    try:
        from goldenpipe.tui.app import GoldenPipeApp
        GoldenPipeApp().run()
    except ImportError:
        console.print("[red]Textual not installed. Run: pip install goldenpipe[tui][/red]")
        raise typer.Exit(code=1)
