"""CLI commands for rollback and run history."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def rollback_cmd(
    run_id: str = typer.Argument(..., help="Run ID to rollback"),
    output_dir: str = typer.Option(".", "--output-dir", help="Directory containing run log"),
) -> None:
    """Rollback a previous merge run by deleting its output files."""
    from goldenmatch.core.rollback import rollback_run

    result = rollback_run(run_id, output_dir)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        if "available_runs" in result:
            console.print(f"Available runs: {', '.join(result['available_runs'][:5])}")
        raise typer.Exit(code=1)

    console.print(f"[#2ecc71]Rolled back run {run_id}[/]")
    if result["deleted"]:
        for f in result["deleted"]:
            console.print(f"  Deleted: {f}")
    if result["not_found"]:
        for f in result["not_found"]:
            console.print(f"  [dim]Not found: {f}[/dim]")


def runs_cmd(
    output_dir: str = typer.Option(".", "--output-dir", help="Directory containing run log"),
) -> None:
    """List previous runs (for rollback)."""
    from goldenmatch.core.rollback import list_runs

    runs = list_runs(output_dir)

    if not runs:
        console.print("[dim]No runs found. Run a dedupe first.[/dim]")
        return

    table = Table(title="Run History", border_style="#d4a017", header_style="bold #d4a017")
    table.add_column("Run ID", style="bold")
    table.add_column("Timestamp")
    table.add_column("Files")
    table.add_column("Status")

    for run in reversed(runs[-10:]):
        status = "[red]rolled back[/]" if run.get("rolled_back") else "[#2ecc71]active[/]"
        ts = run.get("timestamp", "")[:19]
        files = str(len(run.get("output_files", [])))
        table.add_row(run["run_id"][:12], ts, files, status)

    console.print(table)


def _clusters_from_df(clusters_df) -> dict[int, dict]:
    """Reconstruct a build_clusters-shaped dict from a clusters CSV.

    Only ``members``/``size`` are populated from the CSV (pair_scores are not
    persisted in the cluster output); the unmerge core fills the rest. When a
    ``--pairs`` file is supplied it is threaded through as ``scored_pairs`` so
    re-clustering uses real edge scores instead of keeping every remaining edge.
    """
    out: dict[int, dict] = {}
    for cid, rows in _group_members(clusters_df).items():
        out[cid] = {
            "members": rows,
            "size": len(rows),
            "oversized": False,
            "pair_scores": {},
            "confidence": 1.0,
            "bottleneck_pair": None,
            "cluster_quality": "strong",
        }
    return out


def _group_members(clusters_df) -> dict[int, list[int]]:
    members: dict[int, list[int]] = {}
    for row in clusters_df.select(["__cluster_id__", "__row_id__"]).iter_rows():
        cid, rid = int(row[0]), int(row[1])
        members.setdefault(cid, []).append(rid)
    return members


def _load_scored_pairs(path: str) -> list[tuple[int, int, float]]:
    import polars as pl

    df = pl.read_csv(path)
    cols = set(df.columns)
    a_col = next((c for c in ("id_a", "__row_id_a__", "a", "row_id_a") if c in cols), None)
    b_col = next((c for c in ("id_b", "__row_id_b__", "b", "row_id_b") if c in cols), None)
    s_col = next((c for c in ("score", "similarity", "weight") if c in cols), None)
    if not (a_col and b_col and s_col):
        raise ValueError(
            "pairs file must have id_a/id_b/score columns (or a/b/score)"
        )
    return [
        (int(a), int(b), float(s))
        for a, b, s in df.select([a_col, b_col, s_col]).iter_rows()
    ]


def unmerge_cmd(
    record_id: int = typer.Argument(..., help="Record row ID to unmerge from its cluster"),
    clusters_file: str = typer.Option(None, "--clusters", help="Path to clusters CSV from a previous run"),
    scored_pairs_file: str = typer.Option(None, "--pairs", help="Path to scored pairs CSV (id_a,id_b,score)"),
    shatter: bool = typer.Option(False, "--shatter", help="Shatter the entire cluster into singletons"),
    threshold: float = typer.Option(0.0, "--threshold", help="Min score threshold for re-clustering"),
    output: str = typer.Option(None, "--output", "-o", help="Where to write the re-clustered CSV (default: <clusters>.unmerged.csv)"),
) -> None:
    """Remove a record from its cluster (per-entity unmerge).

    The record becomes a singleton. Remaining cluster members are re-clustered
    using their stored pair scores. Use --shatter to break the entire cluster.
    """
    import polars as pl

    from goldenmatch.core.cluster import unmerge_cluster, unmerge_record

    if not clusters_file:
        console.print(
            "[red]Error:[/red] --clusters is required.\n"
            "[dim]Generate a clusters CSV with: goldenmatch dedupe --output-clusters[/dim]"
        )
        raise typer.Exit(code=2)

    clusters_df = pl.read_csv(clusters_file)
    if "__row_id__" not in clusters_df.columns or "__cluster_id__" not in clusters_df.columns:
        console.print(
            "[red]Error:[/red] clusters file must contain __row_id__ and __cluster_id__ columns."
        )
        raise typer.Exit(code=2)

    target_row = clusters_df.filter(pl.col("__row_id__") == record_id)
    if target_row.height == 0:
        console.print(f"[red]Record {record_id} not found in clusters file.[/red]")
        raise typer.Exit(code=1)

    cluster_id = int(target_row["__cluster_id__"][0])
    clusters = _clusters_from_df(clusters_df)
    before_members = clusters[cluster_id]["members"]
    console.print(f"[#d4a017]Unmerge record {record_id}[/]")
    console.print(f"  Found in cluster {cluster_id} ({len(before_members)} members)")

    scored_pairs = _load_scored_pairs(scored_pairs_file) if scored_pairs_file else None

    if shatter:
        console.print(
            f"  [bold yellow]Shattering cluster {cluster_id} into "
            f"{len(before_members)} singletons[/]"
        )
        clusters = unmerge_cluster(cluster_id, clusters)
    else:
        console.print(f"  [bold]Removing record {record_id} from cluster[/]")
        clusters = unmerge_record(
            record_id, clusters, threshold, scored_pairs=scored_pairs
        )

    # Re-assign cluster ids onto the source rows and write the result out.
    row_to_cid = {
        rid: cid for cid, cinfo in clusters.items() for rid in cinfo["members"]
    }
    updated = clusters_df.with_columns(
        pl.col("__row_id__")
        .replace_strict(row_to_cid, default=None)
        .alias("__cluster_id__")
    )

    out_path = output or f"{clusters_file.rsplit('.', 1)[0]}.unmerged.csv"
    updated.write_csv(out_path)

    new_cid = row_to_cid.get(record_id)
    console.print(
        f"[#2ecc71]Done.[/] {len(clusters)} clusters after unmerge; "
        f"record {record_id} now in cluster {new_cid}."
    )
    console.print(f"  Wrote {out_path}")
