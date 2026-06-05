"""CLI incremental command for GoldenMatch."""
from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goldenmatch.config.loader import load_config

console = Console()
err_console = Console(stderr=True)


def incremental_cmd(
    base_file: str = typer.Argument(..., help="Base dataset file path"),
    new_records: Path = typer.Option(..., "--new-records", "-n", help="New records CSV to match"),
    config: Path = typer.Option(..., "--config", "-c", help="Config YAML path"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output CSV path"),
    threshold: float | None = typer.Option(None, "--threshold", "-t", help="Override threshold"),
    exclude_columns: str | None = typer.Option(
        None, "--exclude-columns",
        help=(
            "Comma-separated columns to skip across the suite. "
            "GoldenMatch never picks these for matchkeys/blocking; "
            "GoldenFlow transforms skip them entirely."
        ),
    ),
) -> None:
    """Match new records against an existing base dataset incrementally."""
    import polars as pl

    from goldenmatch._exclusions_schema import merge_exclude_columns_into_config
    from goldenmatch.core.incremental import run_incremental

    if not new_records.exists():
        err_console.print(f"[red]New records file not found: {new_records}[/red]")
        raise typer.Exit(1)

    cfg = load_config(str(config))

    _resolved_excludes = merge_exclude_columns_into_config(cfg, exclude_columns)
    if _resolved_excludes:
        err_console.print(
            f"[dim]exclude_columns ({len(_resolved_excludes)}): "
            f"{', '.join(_resolved_excludes)}[/dim]",
        )

    console.print("[bold]Matching new records against the base dataset...[/bold]")
    t0 = time.perf_counter()
    summary = run_incremental(base_file, str(new_records), cfg, threshold=threshold)
    elapsed = time.perf_counter() - t0

    table = Table(title="Incremental Match Results")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", justify="right")
    table.add_row("New records processed", str(summary["new_records"]))
    table.add_row("Matched to base", str(summary["matched_to_base"]))
    table.add_row("New entities", str(summary["new_entities"]))
    table.add_row("Total match pairs", str(summary["total_pairs"]))
    table.add_row("Time", f"{elapsed:.2f}s")
    console.print(table)

    if output and summary["matches"]:
        pl.DataFrame(summary["matches"]).write_csv(output)
        console.print(f"\n[green]Results saved to {output}[/green]")
    elif output:
        console.print("\n[yellow]No matches found - no output written[/yellow]")
