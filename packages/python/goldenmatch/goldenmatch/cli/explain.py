"""CLI explain command -- natural-language explanation of a pair or cluster.

Surfaces ``core.explain.explain_pair_nl`` / ``explain_cluster_nl`` (zero LLM
cost, template-based) which previously had no CLI front door.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


def explain_cmd(
    files: list[str] = typer.Argument(..., help="Input files (path or path:source_name)"),
    config: Path = typer.Option(..., "--config", "-c", help="Config YAML path"),
    pair: str = typer.Option(None, "--pair", help="Explain a pair: 'id_a,id_b'"),
    cluster: int = typer.Option(None, "--cluster", help="Explain a cluster by id"),
) -> None:
    """Explain why a pair matched, or summarize a cluster, in plain language.

    Provide exactly one of --pair or --cluster.
    """
    from goldenmatch.cli.dedupe import _parse_file_source
    from goldenmatch.config.loader import load_config
    from goldenmatch.core.explain import explain_cluster_nl
    from goldenmatch.core.lineage import build_lineage
    from goldenmatch.tui.engine import MatchEngine

    if (pair is None) == (cluster is None):
        console.print("[red]Error:[/red] provide exactly one of --pair or --cluster.")
        raise typer.Exit(code=2)

    cfg = load_config(str(config))
    paths = [_parse_file_source(f)[0] for f in files]

    console.print("[bold]Running pipeline...[/bold]")
    engine = MatchEngine(paths)
    result = engine.run_full(cfg)
    df = engine.data
    clusters = result.clusters
    scored_pairs = result.scored_pairs

    if pair is not None:
        try:
            id_a, id_b = (int(x) for x in pair.split(","))
        except ValueError:
            console.print("[red]Error:[/red] --pair must be 'id_a,id_b' (two integers).")
            raise typer.Exit(code=2) from None
        lineage = build_lineage(
            scored_pairs, df, cfg.get_matchkeys(), clusters, natural_language=True
        )
        want = {(min(id_a, id_b), max(id_a, id_b))}
        match = next(
            (
                r for r in lineage
                if (min(r["row_id_a"], r["row_id_b"]), max(r["row_id_a"], r["row_id_b"])) in want
            ),
            None,
        )
        if match is None:
            console.print(
                f"[yellow]No scored pair ({id_a}, {id_b}).[/yellow] "
                "[dim]The pair may have scored below threshold or not been blocked together.[/dim]"
            )
            raise typer.Exit(code=1)
        console.print(Panel(
            match.get("explanation") or "(no explanation available)",
            title=f"Pair ({id_a}, {id_b}) · score {match.get('score', 0.0):.3f}",
            border_style="#d4a017",
        ))
        return

    cinfo = clusters.get(cluster)
    if cinfo is None:
        console.print(f"[yellow]No cluster {cluster}.[/yellow]")
        raise typer.Exit(code=1)
    summary = explain_cluster_nl(cinfo, df, cfg.get_matchkeys())
    console.print(Panel(
        summary,
        title=f"Cluster {cluster} · {cinfo.get('size', '?')} records",
        border_style="#d4a017",
    ))
