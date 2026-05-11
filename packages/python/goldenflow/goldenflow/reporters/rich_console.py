from __future__ import annotations

from rich.console import Console
from rich.table import Table

from goldenflow.engine.manifest import Manifest
from goldenflow.engine.profiler_bridge import DatasetProfile

console = Console()


def print_profile(profile: DatasetProfile) -> None:
    table = Table(title=f"Profile: {profile.file_path or '<dataframe>'}")
    table.add_column("Column", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Nulls", style="yellow")
    table.add_column("Unique", style="magenta")
    table.add_column("Sample", style="dim")

    for col in profile.columns:
        table.add_row(
            col.name,
            col.inferred_type,
            f"{col.null_count} ({col.null_pct:.0%})",
            f"{col.unique_count} ({col.unique_pct:.0%})",
            ", ".join(col.sample_values[:3]),
        )

    console.print(table)
    console.print(f"\n[bold]{profile.row_count}[/bold] rows, [bold]{profile.column_count}[/bold] columns")


def print_manifest(manifest: Manifest) -> None:
    if not manifest.records and not manifest.errors:
        console.print("[dim]No transforms applied.[/dim]")
        return

    table = Table(title="Transforms Applied")
    table.add_column("Column", style="cyan")
    table.add_column("Transform", style="green")
    table.add_column("Affected", style="yellow")
    table.add_column("Before", style="dim")
    table.add_column("After", style="bold")

    for r in manifest.records:
        table.add_row(
            r.column,
            r.transform,
            f"{r.affected_rows}/{r.total_rows}",
            ", ".join(r.sample_before[:2]),
            ", ".join(r.sample_after[:2]),
        )

    console.print(table)

    if manifest.errors:
        console.print(f"\n[bold red]{len(manifest.errors)} errors:[/bold red]")
        for e in manifest.errors:
            console.print(f"  [red]{e.column}[/red] / {e.transform}: {e.error}")


def print_diff(diff_result) -> None:
    from goldenflow.engine.differ import DiffResult
    d: DiffResult = diff_result
    console.print(f"Rows: {d.row_count_before} → {d.row_count_after}")
    console.print(f"Total changes: [bold]{d.total_changes}[/bold]")
    if d.added_columns:
        console.print(f"Added columns: [green]{', '.join(d.added_columns)}[/green]")
    if d.removed_columns:
        console.print(f"Removed columns: [red]{', '.join(d.removed_columns)}[/red]")
    if d.changed_columns:
        console.print(f"Changed columns: [yellow]{', '.join(d.changed_columns)}[/yellow]")
