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
    from goldenmatch.cli.dedupe import _parse_file_source
    from goldenmatch.core.anomaly import detect_anomalies
    from goldenmatch.core.io_arrow import read_files_arrow

    if sensitivity not in ("low", "medium", "high"):
        console.print("[red]Error:[/red] --sensitivity must be low, medium, or high.")
        raise typer.Exit(code=2)

    # Arrow ingest, not polars: polars is an OPTIONAL extra since v3.1.0, so
    # `import polars` here made this command raise ImportError on a default
    # install. `read_files_arrow` keeps the semantics that mattered -- the old
    # `how="diagonal"` concat (union of columns, nulls for the gaps) and the
    # Int64 __row_id__. detect_anomalies already reads through `to_frame`, so
    # it takes the arrow table unchanged.
    df = read_files_arrow(
        [_parse_file_source(raw) for raw in files],
        source_column="__source__",
        row_id_column="__row_id__",
    )

    anomalies = detect_anomalies(df, sensitivity=sensitivity)

    if not anomalies:
        console.print(f"[#2ecc71]No anomalies found[/] at sensitivity '{sensitivity}'.")
        return

    if output:
        from pathlib import Path

        import pyarrow as pa

        from goldenmatch.output._csv_arrow import write_csv_polars_parity

        write_csv_polars_parity(pa.Table.from_pylist(anomalies), Path(output))
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
