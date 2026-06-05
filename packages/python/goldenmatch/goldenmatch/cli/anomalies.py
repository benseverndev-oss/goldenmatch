"""CLI anomalies command -- standalone suspicious-record detection.

Surfaces ``core.anomaly.detect_anomalies`` directly. Previously this was only
reachable as the ``dedupe --anomalies`` flag; here it runs on its own without
a dedupe pipeline.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def anomalies_cmd(
    files: list[str] = typer.Argument(..., help="Input files (path or path:source_name)"),
    sensitivity: str = typer.Option("medium", "--sensitivity", "-s", help="low, medium, or high"),
    output: str = typer.Option(None, "--output", "-o", help="Write anomalies to a CSV instead of printing"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows to print"),
) -> None:
    """Detect suspicious/fake records (test emails, repeated digits, bad ZIPs, ...)."""
    import polars as pl

    from goldenmatch.cli.dedupe import _parse_file_source
    from goldenmatch.core.anomaly import detect_anomalies
    from goldenmatch.core.ingest import load_file

    if sensitivity not in ("low", "medium", "high"):
        console.print("[red]Error:[/red] --sensitivity must be low, medium, or high.")
        raise typer.Exit(code=2)

    frames = []
    for raw in files:
        path, source = _parse_file_source(raw)
        lf = load_file(path).with_columns(pl.lit(source).alias("__source__"))
        frames.append(lf.collect())
    df = pl.concat(frames, how="diagonal").with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64)
    )

    anomalies = detect_anomalies(df, sensitivity=sensitivity)

    if not anomalies:
        console.print(f"[#2ecc71]No anomalies found[/] at sensitivity '{sensitivity}'.")
        return

    if output:
        pl.DataFrame(anomalies).write_csv(output)
        console.print(f"[#2ecc71]Wrote {len(anomalies)} anomalies[/] to {output}")
        return

    table = Table(
        title=f"{len(anomalies)} anomalies (sensitivity: {sensitivity})",
        border_style="#d4a017",
        header_style="bold #d4a017",
    )
    table.add_column("Row")
    table.add_column("Column")
    table.add_column("Type")
    table.add_column("Value")
    table.add_column("Severity")
    table.add_column("Reason")
    sev_color = {"high": "red", "medium": "yellow", "low": "#8892a0"}
    for a in anomalies[:limit]:
        sev = str(a.get("severity", ""))
        table.add_row(
            str(a.get("row_id", "")),
            str(a.get("column", "")),
            str(a.get("type", "")),
            str(a.get("value", ""))[:40],
            f"[{sev_color.get(sev, 'white')}]{sev}[/]",
            str(a.get("reason", ""))[:60],
        )
    console.print(table)
    if len(anomalies) > limit:
        console.print(f"[dim]... {len(anomalies) - limit} more. Use --output to dump all.[/dim]")
