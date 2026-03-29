"""Interactive setup wizard for GoldenFlow config."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()


def run_wizard(data_path: Path | None = None, output_path: Path = Path("goldenflow.yaml")) -> None:
    """Interactive config generation wizard."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.profiler_bridge import profile_dataframe
    from goldenflow.engine.selector import select_transforms
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
    from goldenflow.config.loader import save_config

    console.print("[bold]GoldenFlow Setup Wizard[/bold]\n")

    # Step 1: Get data file
    if data_path is None:
        path_str = Prompt.ask("Path to your data file")
        data_path = Path(path_str)

    if not data_path.exists():
        console.print(f"[red]File not found: {data_path}[/red]")
        return

    # Step 2: Profile the data
    console.print(f"\n[dim]Profiling {data_path.name}...[/dim]")
    df = read_file(data_path)
    profile = profile_dataframe(df, file_path=str(data_path))

    # Step 3: Show profile summary
    table = Table(title="Column Profile")
    table.add_column("Column", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Nulls", style="yellow")
    table.add_column("Suggested Transforms", style="magenta")

    column_transforms: dict[str, list[str]] = {}
    for col in profile.columns:
        selected = select_transforms(col)
        transform_names = [t.name for t in selected]
        column_transforms[col.name] = transform_names
        table.add_row(
            col.name,
            col.inferred_type,
            f"{col.null_count} ({col.null_pct:.0%})",
            ", ".join(transform_names[:3]) or "[dim]none[/dim]",
        )

    console.print(table)

    # Step 4: Let user confirm/modify per column
    transforms: list[TransformSpec] = []
    console.print("\n[bold]Configure transforms per column:[/bold]\n")

    for col_name, suggested in column_transforms.items():
        if not suggested:
            continue
        if Confirm.ask(f"  Apply [{', '.join(suggested)}] to [cyan]{col_name}[/cyan]?", default=True):
            transforms.append(TransformSpec(column=col_name, ops=suggested))

    # Step 5: Ask about renames, drops
    renames: dict[str, str] = {}
    if Confirm.ask("\nRename any columns?", default=False):
        while True:
            old = Prompt.ask("  Old name (or 'done')")
            if old.lower() == "done":
                break
            new = Prompt.ask(f"  New name for '{old}'")
            renames[old] = new

    drop: list[str] = []
    if Confirm.ask("Drop any columns?", default=False):
        drop_str = Prompt.ask("  Column names to drop (comma-separated)")
        drop = [c.strip() for c in drop_str.split(",") if c.strip()]

    # Step 6: Save config
    config = GoldenFlowConfig(
        source=str(data_path),
        transforms=transforms,
        renames=renames,
        drop=drop,
    )
    save_config(config, output_path)
    console.print(f"\n[green]Config saved to {output_path}[/green]")
    console.print(f"[dim]Run: goldenflow transform {data_path} -c {output_path}[/dim]")
