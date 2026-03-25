"""GoldenPipe CLI."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="goldenpipe", help="Golden Suite orchestrator -- Check, Flow, Match in one pipeline.")
console = Console()


@app.command()
def run(
    source: str = typer.Argument(..., help="Path to CSV, Excel, or Parquet file"),
    skip_flow: bool = typer.Option(False, "--skip-flow", help="Skip GoldenFlow transformation"),
    skip_match: bool = typer.Option(False, "--skip-match", help="Skip GoldenMatch deduplication"),
    strategy: str = typer.Option(None, "--strategy", help="Force matching strategy (pprl, auto)"),
    output: str = typer.Option(None, "--output", "-o", help="Write golden records to file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show reasoning for each stage"),
):
    """Run the full Golden Suite pipeline on a file."""
    from goldenpipe.pipeline import Pipeline

    if not Path(source).exists():
        console.print(f"[red]File not found: {source}[/red]")
        raise typer.Exit(1)

    pipe = Pipeline(source, strategy=strategy)

    with console.status("[bold yellow]Running GoldenPipe..."):
        pipe.check()
        if not skip_flow:
            pipe.flow()
        if not skip_match:
            pipe.match()

    result = pipe.result

    # Summary table
    table = Table(title="GoldenPipe Result", show_header=True)
    table.add_column("Stage", style="bold")
    table.add_column("Status")
    table.add_column("Detail")

    # Check
    if result.check is not None:
        n = len(getattr(result.check, "findings", []) or [])
        table.add_row("Check", "[green]Done[/green]", f"{n} findings")
    elif "check" in str(result.skipped):
        table.add_row("Check", "[yellow]Skipped[/yellow]", result.reasoning.get("check", ""))
    else:
        table.add_row("Check", "[red]Failed[/red]", result.reasoning.get("check", ""))

    # Flow
    if result.transform is not None:
        table.add_row("Flow", "[green]Done[/green]", result.reasoning.get("flow", ""))
    elif "flow" in result.skipped:
        table.add_row("Flow", "[yellow]Skipped[/yellow]", result.reasoning.get("flow", ""))
    elif skip_flow:
        table.add_row("Flow", "[dim]Skipped[/dim]", "User requested --skip-flow")
    else:
        table.add_row("Flow", "[red]Failed[/red]", result.reasoning.get("flow", ""))

    # Match
    if result.match is not None:
        clusters = getattr(result.match, "total_clusters", "?")
        rate = getattr(result.match, "match_rate", 0)
        table.add_row("Match", "[green]Done[/green]", f"{clusters} clusters, {rate:.1%} match rate")
    elif "match" in result.skipped:
        table.add_row("Match", "[yellow]Skipped[/yellow]", result.reasoning.get("match", ""))
    elif skip_match:
        table.add_row("Match", "[dim]Skipped[/dim]", "User requested --skip-match")
    else:
        table.add_row("Match", "[red]Failed[/red]", result.reasoning.get("match", ""))

    console.print()
    console.print(table)
    console.print(f"\n[bold]Status:[/bold] {result.status} | [bold]Input:[/bold] {result.input_rows} rows | [bold]Source:[/bold] {result.source}")

    if result.errors:
        console.print(f"\n[red]Errors:[/red]")
        for err in result.errors:
            console.print(f"  {err}")

    if verbose:
        console.print(f"\n[bold]Reasoning:[/bold]")
        for stage, reason in result.reasoning.items():
            console.print(f"  {stage}: {reason}")
        console.print(f"\n[bold]Timing:[/bold]")
        for stage, secs in result.timing.items():
            console.print(f"  {stage}: {secs:.2f}s")

    # Write output
    if output and result.match is not None:
        golden = getattr(result.match, "golden", None)
        if golden is not None:
            golden.write_csv(output)
            console.print(f"\n[green]Golden records written to {output}[/green]")


if __name__ == "__main__":
    app()
