"""CLI lineage command -- build + persist match lineage for a run.

Surfaces ``core.lineage.build_lineage`` / ``save_lineage`` (the per-pair audit
trail) which previously had no CLI front door (only the MCP ``lineage`` tool).
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console()


def lineage_cmd(
    files: list[str] = typer.Argument(..., help="Input files (path or path:source_name)"),
    config: Path = typer.Option(..., "--config", "-c", help="Config YAML path"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help="Write lineage.json here (default: print a summary)"),
    run_name: str = typer.Option("lineage", "--run-name", help="Run name for the lineage file"),
    max_pairs: int = typer.Option(10000, "--max-pairs", help="Cap on lineage records (0 = no cap)"),
    natural_language: bool = typer.Option(False, "--nl", help="Include natural-language explanations"),
) -> None:
    """Build the per-pair match lineage for a dedupe run and save or summarize it."""
    from goldenmatch.cli.dedupe import _parse_file_source
    from goldenmatch.config.loader import load_config
    from goldenmatch.core.lineage import build_lineage, save_lineage
    from goldenmatch.tui.engine import MatchEngine

    cfg = load_config(str(config))
    paths = [_parse_file_source(f)[0] for f in files]

    console.print("[bold]Running pipeline to build lineage...[/bold]")
    engine = MatchEngine(paths)
    result = engine.run_full(cfg)
    if not result.scored_pairs:
        console.print("[yellow]No scored pairs to build lineage from.[/yellow]")
        raise typer.Exit(code=1)

    lineage = build_lineage(
        result.scored_pairs,
        engine.data,
        cfg.get_matchkeys(),
        result.clusters,
        max_pairs=max_pairs,
        natural_language=natural_language,
    )

    if output_dir:
        path = save_lineage(lineage, output_dir, run_name=run_name)
        console.print(f"[#2ecc71]Wrote {len(lineage)} lineage records[/] to {path}")
        return

    console.print(f"[#2ecc71]Built {len(lineage)} lineage records.[/]")
    console.print("[dim]Pass --output-dir to persist them, or --nl for explanations.[/dim]")
    for rec in lineage[:3]:
        a, b = rec.get("row_id_a"), rec.get("row_id_b")
        score = rec.get("score", 0.0)
        why = rec.get("explanation") or ""
        console.print(f"  ({a}, {b}) score={score:.3f} {why}")
