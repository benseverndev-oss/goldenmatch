"""CLI review command -- human-in-the-loop triage of borderline pairs.

Surfaces two sources of pairs for a steward to decide:

1. Fresh borderline pairs from a pipeline run (when input files are given),
   gated into the review band via :func:`gate_pairs`.
2. Any pairs already pending in the persistent SQLite review queue
   (``.goldenmatch/reviews.db`` by default) -- e.g. stale corrections
   re-enqueued by Learning Memory.

Decisions are written through :class:`ReviewQueue`, which records an
``approve``/``reject`` correction into the Learning Memory store so the next
run learns from them.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def review_cmd(
    files: list[str] = typer.Argument(
        None, help="Input files (path or path:source_name). Omit to review only the pending queue."
    ),
    config: Path = typer.Option(..., "--config", "-c", help="Config YAML path"),
    job: str = typer.Option("review", "--job", help="Review-queue job name for freshly-gated pairs"),
    queue_path: str = typer.Option(
        None,
        "--queue-path",
        help="SQLite review queue path (default: sibling of the config's memory store, else .goldenmatch/review_queue.db)",
    ),
    memory_path: str = typer.Option(".goldenmatch/memory.db", "--memory-path", help="Learning Memory SQLite path"),
    merge_threshold: float = typer.Option(0.95, "--merge-threshold", help="Scores above this auto-merge (skip review)"),
    review_threshold: float = typer.Option(0.75, "--review-threshold", help="Scores >= this go to review"),
    decided_by: str = typer.Option("cli", "--decided-by", help="Steward identifier recorded on each decision"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max pairs to review this session"),
) -> None:
    """Review borderline pairs one at a time and record approve/reject decisions.

    Type y (approve/match), n (reject/no match), s (skip), or q (quit).
    Decisions feed Learning Memory and are applied on the next run.
    """
    import polars as pl

    from goldenmatch.config.loader import load_config
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.core.pipeline import _derive_review_queue_path
    from goldenmatch.core.review_queue import ReviewQueue, gate_pairs, why_for_correction

    cfg = load_config(str(config))
    matchkey_fields = _matchkey_fields(cfg)

    # Default the queue to the same sibling file the pipeline enqueues stale
    # corrections to ("memory_stale" job), so `review` surfaces them.
    if queue_path is None:
        queue_path = _derive_review_queue_path(cfg) or ".goldenmatch/review_queue.db"

    df: pl.DataFrame | None = None
    if files:
        from goldenmatch.cli.dedupe import _parse_file_source, _resolve_column_maps
        from goldenmatch.core.pipeline import run_dedupe

        parsed = [_parse_file_source(f) for f in files]
        file_specs = _resolve_column_maps(parsed, cfg)
        console.print("[bold]Running pipeline to surface borderline pairs...[/bold]")
        result = run_dedupe(file_specs, cfg)
        df = result.get("_df")
        scored_pairs = result.get("scored_pairs", []) or []
        _, review_pairs, _ = gate_pairs(scored_pairs, merge_threshold, review_threshold)
        console.print(f"[dim]{len(review_pairs)} pair(s) fell in the review band.[/dim]")
    else:
        review_pairs = []

    store = MemoryStore(backend="sqlite", path=memory_path)
    rq = ReviewQueue(
        backend="sqlite",
        path=queue_path,
        memory_store=store,
        df=df,
        matchkey_fields=matchkey_fields,
    )

    # Enqueue freshly-gated pairs (INSERT OR REPLACE dedups against the queue).
    for a, b, score in review_pairs:
        explanation = why_for_correction(
            a, b, df, matchkey_fields, score=score, use_llm=False
        )
        rq.add(job, a, b, score, explanation)

    # Surface both the fresh job and the pipeline's stale-correction job.
    seen: set[tuple[int, int]] = set()
    pending = []
    for job_name in (job, "memory_stale"):
        for item in rq.list_pending(job_name):
            key = (item.id_a, item.id_b)
            if key not in seen:
                seen.add(key)
                pending.append(item)
    if not pending:
        console.print(
            "[yellow]Nothing to review.[/yellow] "
            "[dim]Pass input files to generate borderline pairs, or check the queue path.[/dim]"
        )
        store.close()
        return

    row_lookup = {}
    display_cols: list[str] = []
    if df is not None:
        row_lookup = {r["__row_id__"]: r for r in df.to_dicts()}
        display_cols = [c for c in df.columns if not c.startswith("__")][:6]

    console.print(
        f"\n[bold]Reviewing {min(len(pending), limit)} of {len(pending)} pending pair(s). "
        f"Type: y=match, n=no match, s=skip, q=quit[/bold]\n"
    )

    approved = rejected = skipped = 0
    for idx, item in enumerate(pending[:limit], start=1):
        table = Table(
            title=f"Pair {idx} (score: {item.score:.3f})",
            show_header=True,
            border_style="#d4a017",
        )
        table.add_column("Field", style="bold")
        table.add_column(f"Record {item.id_a}", style="cyan")
        table.add_column(f"Record {item.id_b}", style="green")
        row_a = row_lookup.get(item.id_a, {})
        row_b = row_lookup.get(item.id_b, {})
        for col in display_cols:
            va = str(row_a.get(col, ""))[:60]
            vb = str(row_b.get(col, ""))[:60]
            table.add_row(col, va, vb)
        console.print(table)
        if item.why:
            console.print(f"[dim]{item.why}[/dim]")

        while True:
            response = console.input("[y/n/s/q] > ").strip().lower()
            if response in ("y", "n", "s", "q"):
                break
            console.print("[dim]Type y, n, s, or q[/dim]")

        if response == "q":
            break
        if response == "s":
            skipped += 1
            continue
        if response == "y":
            rq.approve(item.job_name, item.id_a, item.id_b, decided_by)
            approved += 1
        else:
            rq.reject(item.job_name, item.id_a, item.id_b, decided_by)
            rejected += 1
        console.print()

    store.close()
    console.print(
        f"\n[#2ecc71]Done.[/] Approved {approved}, rejected {rejected}, skipped {skipped}."
    )
    console.print(
        "[dim]Decisions recorded to Learning Memory. "
        "Run 'goldenmatch memory learn' or re-run dedupe to apply them.[/dim]"
    )


def _matchkey_fields(cfg) -> list[str]:
    """Collect the distinct field names referenced by the config's matchkeys."""
    fields: list[str] = []
    for mk in cfg.get_matchkeys():
        for f in getattr(mk, "fields", None) or []:
            name = getattr(f, "field", None) or getattr(f, "resolved_field", None)
            if name and name not in fields:
                fields.append(name)
    return fields
