"""CLI label command -- build ground truth by labeling pairs interactively."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from goldenmatch.cli.dedupe import _parse_file_source, _resolve_column_maps
from goldenmatch.config.loader import load_config

console = Console()


def label_cmd(
    files: list[str] = typer.Argument(..., help="Input files (path or path:source_name)"),
    config: Path = typer.Option(..., "--config", "-c", help="Config YAML path"),
    output: Path = typer.Option("ground_truth.csv", "--output", "-o", help="Output ground truth CSV"),
    n: int = typer.Option(50, "--n", "-n", help="Number of pairs to label"),
    strategy: str = typer.Option("borderline", "--strategy", help="Pair selection: borderline, random, or hardest"),
    append: bool = typer.Option(False, "--append", "-a", help="Append to existing ground truth file"),
) -> None:
    """Build ground truth by labeling record pairs interactively.

    Shows pairs one at a time. Type y (match), n (no match), or s (skip).
    Saves labeled pairs to a CSV for use with 'goldenmatch evaluate'.
    """
    from goldenmatch.core.pipeline import run_dedupe

    cfg = load_config(str(config))
    parsed = [_parse_file_source(f) for f in files]
    file_specs = _resolve_column_maps(parsed, cfg)

    console.print("[bold]Running pipeline to generate candidate pairs...[/bold]")
    result = run_dedupe(file_specs, cfg)

    # Candidate pairs come from the pipeline's scored-pair stream (SP3), not a
    # reconstruction from cluster pair_scores.
    # #2417: via the helper -- `scored_pairs` is None on the B2c FS path
    # (the Arrow table is the backing), so a bare `.get` reads empty.
    from goldenmatch.core.pairs import materialize_scored_pairs
    all_pairs = materialize_scored_pairs(result)

    if not all_pairs:
        console.print("[yellow]No pairs found. Check your config.[/yellow]")
        raise typer.Exit(1)

    # Select pairs based on strategy
    if strategy == "borderline":
        # Sort by distance from 0.85 (most ambiguous first)
        all_pairs.sort(key=lambda p: abs(p[2] - 0.85))
    elif strategy == "hardest":
        # Lowest scores first (hardest to decide)
        all_pairs.sort(key=lambda p: p[2])
    else:
        # Random
        import random
        random.shuffle(all_pairs)

    pairs_to_label = all_pairs[:n * 2]  # extra buffer for skips

    # Build row lookup
    df = result.get("_df")
    if df is None:
        # Reconstruct from files
        from goldenmatch.core.autofix import auto_fix_dataframe
        from goldenmatch.core.io_arrow import read_files_arrow

        # Arrow ingest: `pl.concat(..., how="diagonal")` here made the whole
        # command raise ImportError on a default install. read_files_arrow keeps
        # the union-of-columns semantics and the Int64 __row_id__.
        combined = read_files_arrow(
            [(spec[0] if isinstance(spec, tuple) else spec) for spec in file_specs],
            row_id_column="__row_id__",
        )
        combined, _ = auto_fix_dataframe(combined)
    else:
        combined = df

    # Through the seam: `to_dicts` is polars-only, and `combined` is an arrow
    # table on a default install (and on the FS path, whatever is installed).
    from goldenmatch.core.frame import to_frame as _to_frame

    _combined_f = _to_frame(combined)
    _all_cols = list(_combined_f.columns)
    row_lookup = {r["__row_id__"]: r for r in _combined_f.select_dicts(_all_cols)}
    display_cols = [c for c in _all_cols if not c.startswith("__")][:6]

    # Load existing labels if appending
    existing = set()
    if append and output.exists():
        from goldenmatch.core.io_arrow import read_table_arrow

        _existing_tbl = read_table_arrow(output)
        for r in _existing_tbl.to_pylist():
            existing.add((int(r["id_a"]), int(r["id_b"])))
        console.print(f"[dim]Loaded {len(existing)} existing labels from {output}[/dim]")

    # Interactive labeling loop
    labels = []
    labeled = 0
    skipped = 0

    console.print(f"\n[bold]Label {n} pairs. Type: y=match, n=no match, s=skip, q=quit[/bold]\n")

    for a, b, score in pairs_to_label:
        if labeled >= n:
            break
        if (a, b) in existing or (b, a) in existing:
            continue

        row_a = row_lookup.get(a, {})
        row_b = row_lookup.get(b, {})

        # Display pair
        table = Table(title=f"Pair {labeled + 1}/{n} (score: {score:.3f})", show_header=True)
        table.add_column("Field", style="bold")
        table.add_column("Record A", style="cyan")
        table.add_column("Record B", style="green")
        for col in display_cols:
            val_a = str(row_a.get(col, ""))[:60]
            val_b = str(row_b.get(col, ""))[:60]
            # Emphasise only when the two agree. Wrapping unconditionally
            # produced "[]value[/]" whenever they differed -- an empty tag and an
            # orphan closer -- and Rich raises MarkupError on it. Labelling
            # exists to compare records that DIFFER, so this crashed on
            # essentially the first pair; `goldenmatch label` did not work at
            # all, with or without polars.
            same = bool(val_a) and val_a.lower() == val_b.lower()
            # escape(): these are DATA values. A record containing "[bold]" would
            # otherwise be parsed as markup and raise on the next row.
            cell_a = escape(val_a)
            cell_b = escape(val_b)
            if same:
                cell_a = f"[bold]{cell_a}[/bold]"
                cell_b = f"[bold]{cell_b}[/bold]"
            table.add_row(col, cell_a, cell_b)
        console.print(table)

        # Get input
        while True:
            response = console.input("[y/n/s/q] > ").strip().lower()
            if response in ("y", "n", "s", "q"):
                break
            console.print("[dim]Type y, n, s, or q[/dim]")

        if response == "q":
            break
        elif response == "s":
            skipped += 1
            continue
        else:
            labels.append({
                "id_a": a,
                "id_b": b,
                "label": 1 if response == "y" else 0,
                "score": round(score, 4),
            })
            labeled += 1
            console.print()

    # Save results
    if labels:
        import pyarrow as pa

        from goldenmatch.core.io_arrow import read_table_arrow
        from goldenmatch.output._csv_arrow import write_csv_polars_parity

        labels_tbl = pa.Table.from_pylist(labels)
        if append and output.exists():
            # promote_options="permissive" is the pl.concat(how="diagonal") this
            # replaces: an older label file with different columns unions rather
            # than raising.
            labels_tbl = pa.concat_tables(
                [read_table_arrow(output), labels_tbl], promote_options="permissive"
            )
        write_csv_polars_parity(labels_tbl, output)

        match_count = sum(1 for l in labels if l["label"] == 1)
        console.print(f"\n[green]Saved {len(labels)} labels to {output}[/green]")
        console.print(f"  Matches: {match_count}, Non-matches: {len(labels) - match_count}, Skipped: {skipped}")
        console.print(f"\n[dim]Use with: goldenmatch evaluate {' '.join(files)} -c {config} --gt {output}[/dim]")
    else:
        console.print("\n[yellow]No labels saved.[/yellow]")
