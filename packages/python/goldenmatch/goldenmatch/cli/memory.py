"""CLI memory commands for GoldenMatch.

Inspect and manage the Learning Memory store: stats, force-learn, export
corrections to CSV, import from CSV, show a single correction.
"""
from __future__ import annotations

import csv
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

import goldenmatch

console = Console()
err_console = Console(stderr=True)

DEFAULT_PATH = ".goldenmatch/memory.db"

# CSV schema for export/import. Matches MemoryStore.Correction fields.
_CSV_FIELDS = [
    "id", "id_a", "id_b", "decision", "source", "trust",
    "field_hash", "record_hash", "original_score",
    "matchkey_name", "reason", "dataset", "created_at",
]

memory_app = typer.Typer(
    name="memory",
    help="Inspect and manage Learning Memory.",
    no_args_is_help=True,
)


@memory_app.command("stats")
def stats_cmd(
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Memory DB path"),
) -> None:
    """Show counts, last learn time, and current adjustments."""
    s = goldenmatch.memory_stats(path=path)

    table = Table(title="Memory Stats")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", justify="right")
    table.add_row("Corrections", str(s["count"]))
    last = s["last_learn_time"]
    table.add_row("Last learn", last.isoformat() if last else "(never)")
    table.add_row("Adjustments", str(len(s["adjustments"])))
    console.print(table)

    if s["adjustments"]:
        adj_table = Table(title="Learned Adjustments")
        adj_table.add_column("Matchkey", style="bold")
        adj_table.add_column("Threshold", justify="right")
        adj_table.add_column("Samples", justify="right")
        adj_table.add_column("Learned at")
        for a in s["adjustments"]:
            thr = a.get("threshold")
            learned = a.get("learned_at")
            adj_table.add_row(
                str(a.get("matchkey_name", "")),
                f"{thr:.3f}" if isinstance(thr, (int, float)) else "-",
                str(a.get("sample_size", 0)),
                learned.isoformat() if hasattr(learned, "isoformat") else str(learned or ""),
            )
        console.print(adj_table)


@memory_app.command("learn")
def learn_cmd(
    matchkey_name: Optional[str] = typer.Option(
        None, "--matchkey-name", help="Limit learning to this matchkey",
    ),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Memory DB path"),
) -> None:
    """Force a learning pass over stored corrections."""
    adjustments = goldenmatch.learn(matchkey_name=matchkey_name, path=path)
    if not adjustments:
        console.print("[dim]No adjustments produced (need >=10 corrections "
                      "with both approve and reject decisions).[/dim]")
        return

    table = Table(title="Learning Pass Results")
    table.add_column("Matchkey", style="bold")
    table.add_column("Threshold", justify="right")
    table.add_column("Samples", justify="right")
    for a in adjustments:
        thr = a.threshold
        table.add_row(
            str(a.matchkey_name),
            f"{thr:.3f}" if isinstance(thr, (int, float)) else "-",
            str(a.sample_size),
        )
    console.print(table)


@memory_app.command("export")
def export_cmd(
    out: str = typer.Argument(..., help="Output CSV path"),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Memory DB path"),
) -> None:
    """Dump all corrections as CSV."""
    store = goldenmatch.get_memory(path)
    try:
        corrections = store.get_corrections()
    finally:
        store.close()

    out_path = Path(out)
    if out_path.parent and str(out_path.parent) != ".":
        out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for c in corrections:
            writer.writerow({
                "id": c.id,
                "id_a": c.id_a,
                "id_b": c.id_b,
                "decision": c.decision,
                "source": c.source,
                "trust": c.trust,
                "field_hash": c.field_hash or "",
                "record_hash": c.record_hash or "",
                "original_score": c.original_score,
                "matchkey_name": c.matchkey_name or "",
                "reason": c.reason or "",
                "dataset": c.dataset or "",
                "created_at": c.created_at.isoformat(),
            })

    console.print(f"[green]Exported {len(corrections)} corrections to {out_path}[/green]")


@memory_app.command("import")
def import_cmd(
    src: str = typer.Argument(..., help="Source CSV path"),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Memory DB path"),
) -> None:
    """Load corrections from CSV. Validates schema before writing."""
    src_path = Path(src)
    if not src_path.exists():
        err_console.print(f"[red]File not found: {src_path}[/red]")
        raise typer.Exit(code=1)

    from goldenmatch.core.memory.store import Correction

    required = {"id_a", "id_b", "decision", "source"}
    rows: list[dict] = []
    with src_path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = required - set(reader.fieldnames or [])
            err_console.print(
                f"[red]Malformed CSV: missing required columns: "
                f"{sorted(missing)}[/red]"
            )
            raise typer.Exit(code=1)
        for i, row in enumerate(reader, start=2):  # line 1 is header
            try:
                row["id_a"] = int(row["id_a"])
                row["id_b"] = int(row["id_b"])
            except (KeyError, ValueError, TypeError) as e:
                err_console.print(
                    f"[red]Malformed CSV at row {i}: cannot parse id_a/id_b ({e})[/red]"
                )
                raise typer.Exit(code=1)
            rows.append(row)

    store = goldenmatch.get_memory(path)
    try:
        for row in rows:
            try:
                trust = float(row.get("trust") or 0.5)
            except (ValueError, TypeError):
                trust = 0.5
            try:
                original_score = float(row.get("original_score") or 0.0)
            except (ValueError, TypeError):
                original_score = 0.0
            created_raw = row.get("created_at") or ""
            try:
                created_at = datetime.fromisoformat(created_raw) if created_raw else datetime.now()
            except ValueError:
                created_at = datetime.now()
            store.add_correction(Correction(
                id=row.get("id") or str(uuid.uuid4()),
                id_a=row["id_a"], id_b=row["id_b"],
                decision=row["decision"],
                source=row["source"],
                trust=trust,
                field_hash=row.get("field_hash") or "",
                record_hash=row.get("record_hash") or "",
                original_score=original_score,
                matchkey_name=row.get("matchkey_name") or None,
                reason=row.get("reason") or None,
                dataset=row.get("dataset") or None,
                created_at=created_at,
            ))
    finally:
        store.close()

    console.print(f"[green]Imported {len(rows)} corrections from {src_path}[/green]")


@memory_app.command("show")
def show_cmd(
    id_a: int = typer.Argument(..., help="First record ID"),
    id_b: int = typer.Argument(..., help="Second record ID"),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Memory DB path"),
) -> None:
    """Pretty-print a single stored correction."""
    store = goldenmatch.get_memory(path)
    try:
        c = store.get_pair_correction(id_a, id_b)
    finally:
        store.close()

    if c is None:
        err_console.print(
            f"[yellow]No correction found for pair ({id_a}, {id_b}).[/yellow]"
        )
        raise typer.Exit(code=1)

    table = Table(title=f"Correction ({c.id_a}, {c.id_b})")
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("id", c.id)
    table.add_row("id_a", str(c.id_a))
    table.add_row("id_b", str(c.id_b))
    table.add_row("decision", c.decision)
    table.add_row("source", c.source)
    table.add_row("trust", f"{c.trust:.2f}")
    table.add_row("matchkey_name", c.matchkey_name or "")
    table.add_row("reason", c.reason or "")
    table.add_row("dataset", c.dataset or "")
    table.add_row("original_score", f"{c.original_score:.3f}")
    table.add_row("field_hash", c.field_hash or "")
    table.add_row("record_hash", c.record_hash or "")
    table.add_row("created_at", c.created_at.isoformat())
    console.print(table)
