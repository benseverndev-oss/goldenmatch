"""CLI: ``goldenmatch identity ...`` -- inspect and manage the Identity Graph."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goldenmatch.identity import (
    IdentityStore,
    find_by_record,
    find_conflicts,
    get_entity,
    history,
    list_entities,
    manual_merge,
    manual_split,
)

console = Console()
err_console = Console(stderr=True)

DEFAULT_PATH = ".goldenmatch/identity.db"

identity_app = typer.Typer(
    name="identity",
    help="Inspect and manage the Identity Graph.",
    no_args_is_help=True,
)


def _open(path: str) -> IdentityStore:
    p = Path(path)
    if not p.exists():
        err_console.print(f"[red]Identity DB not found:[/red] {path}")
        raise typer.Exit(code=2)
    return IdentityStore(path=path)


@identity_app.command("list")
def list_cmd(
    path: str = typer.Option(DEFAULT_PATH, "--path"),
    dataset: str | None = typer.Option(None, "--dataset"),
    status: str | None = typer.Option(None, "--status"),
    limit: int = typer.Option(50, "--limit"),
    offset: int = typer.Option(0, "--offset"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List identities (most recently updated first)."""
    with _open(path) as s:
        rows = list_entities(s, dataset=dataset, status=status, limit=limit, offset=offset)
    if json_out:
        console.print_json(json.dumps(rows))
        return
    table = Table(title=f"Identities ({len(rows)})")
    table.add_column("entity_id", style="cyan")
    table.add_column("status")
    table.add_column("conf", justify="right")
    table.add_column("dataset")
    table.add_column("updated_at")
    for r in rows:
        table.add_row(
            r["entity_id"][:8] + "...",
            r["status"],
            f"{r['confidence']:.3f}" if r.get("confidence") is not None else "-",
            r.get("dataset") or "-",
            r["updated_at"],
        )
    console.print(table)


@identity_app.command("show")
def show_cmd(
    entity_id: str = typer.Argument(...),
    path: str = typer.Option(DEFAULT_PATH, "--path"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show an identity with members, edges, and recent events."""
    with _open(path) as s:
        view = get_entity(s, entity_id)
    if view is None:
        err_console.print(f"[red]Not found:[/red] {entity_id}")
        raise typer.Exit(code=1)
    if json_out:
        console.print_json(json.dumps(view.to_dict()))
        return
    console.print(f"[bold cyan]{view.node.entity_id}[/bold cyan]  status={view.node.status}")
    console.print(f"  confidence: {view.node.confidence}")
    console.print(f"  dataset:    {view.node.dataset}")
    console.print(f"  records:    {len(view.records)}, edges: {len(view.edges)}, events: {len(view.events)}")
    if view.records:
        t = Table(title="Members")
        t.add_column("record_id", style="cyan")
        t.add_column("source")
        t.add_column("hash", style="dim")
        for r in view.records:
            t.add_row(r.record_id, r.source, r.record_hash[:12])
        console.print(t)


@identity_app.command("resolve")
def resolve_cmd(
    record_id: str = typer.Argument(..., help="`{source}:{pk}` to look up"),
    path: str = typer.Option(DEFAULT_PATH, "--path"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Resolve a record_id to its identity."""
    with _open(path) as s:
        view = find_by_record(s, record_id)
    if view is None:
        err_console.print(f"[yellow]No identity for record:[/yellow] {record_id}")
        raise typer.Exit(code=1)
    if json_out:
        console.print_json(json.dumps(view.to_dict()))
    else:
        console.print(f"{record_id} -> [cyan]{view.node.entity_id}[/cyan] ({view.node.status})")


@identity_app.command("history")
def history_cmd(
    entity_id: str = typer.Argument(...),
    path: str = typer.Option(DEFAULT_PATH, "--path"),
    limit: int = typer.Option(50, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show the temporal event log for an identity."""
    with _open(path) as s:
        events = history(s, entity_id, limit=limit)
    if json_out:
        console.print_json(json.dumps(events))
        return
    table = Table(title=f"History: {entity_id[:8]}...")
    table.add_column("when")
    table.add_column("kind", style="cyan")
    table.add_column("run")
    table.add_column("payload", style="dim")
    for ev in events:
        table.add_row(
            ev["recorded_at"],
            ev["kind"],
            ev["run_name"] or "-",
            json.dumps(ev["payload"]) if ev["payload"] else "-",
        )
    console.print(table)


@identity_app.command("conflicts")
def conflicts_cmd(
    path: str = typer.Option(DEFAULT_PATH, "--path"),
    dataset: str | None = typer.Option(None, "--dataset"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List conflicting evidence edges."""
    with _open(path) as s:
        rows = find_conflicts(s, dataset=dataset)
    if json_out:
        console.print_json(json.dumps(rows))
        return
    if not rows:
        console.print("[green]No conflicts.[/green]")
        return
    t = Table(title=f"Conflicts ({len(rows)})")
    for col in ("entity_id", "record_a_id", "record_b_id", "score", "run_name", "recorded_at"):
        t.add_column(col)
    for r in rows:
        t.add_row(
            r["entity_id"][:8] + "...",
            r["record_a_id"], r["record_b_id"],
            f"{r['score']:.3f}" if r["score"] is not None else "-",
            r["run_name"] or "-",
            r["recorded_at"],
        )
    console.print(t)


@identity_app.command("merge")
def merge_cmd(
    keep: str = typer.Argument(..., help="entity_id to keep"),
    absorb: str = typer.Argument(..., help="entity_id to absorb"),
    reason: str | None = typer.Option(None, "--reason"),
    path: str = typer.Option(DEFAULT_PATH, "--path"),
) -> None:
    """Manually merge two identities. Records from ``absorb`` move to ``keep``."""
    with _open(path) as s:
        out = manual_merge(s, keep, absorb, reason=reason)
    console.print(f"[green]Merged[/green] {absorb[:8]}... -> {keep[:8]}... at {out['at']}")


@identity_app.command("split")
def split_cmd(
    entity_id: str = typer.Argument(...),
    record_ids: list[str] = typer.Argument(..., help="record_ids to move to a new identity"),
    reason: str | None = typer.Option(None, "--reason"),
    path: str = typer.Option(DEFAULT_PATH, "--path"),
) -> None:
    """Manually split records off into a new identity."""
    with _open(path) as s:
        out = manual_split(s, entity_id, record_ids, reason=reason)
    console.print(f"[green]Split[/green] {len(out['moved'])} records -> new id {out['new_entity_id'][:8]}...")


@identity_app.command("migrate")
def migrate_cmd(
    dsn: str = typer.Option(
        ...,
        "--dsn",
        envvar="GOLDENMATCH_IDENTITY_DSN",
        help="Postgres DSN; can also be set via GOLDENMATCH_IDENTITY_DSN.",
    ),
    stamp_existing: bool = typer.Option(
        False,
        "--stamp-existing",
        help="Stamp an existing v1 schema at revision 0001 without re-creating tables.",
    ),
    revision: str = typer.Option(
        "head",
        "--revision",
        help="Target revision (default: head).",
    ),
) -> None:
    """Run Alembic migrations on the Identity Graph schema."""
    import pathlib

    from alembic import command
    from alembic.config import Config

    cfg_path = pathlib.Path(__file__).parent.parent / "db" / "alembic.ini"
    cfg = Config(str(cfg_path))
    cfg.set_main_option("sqlalchemy.url", dsn)
    cfg.set_main_option(
        "script_location",
        str(pathlib.Path(__file__).parent.parent / "db" / "alembic"),
    )
    if stamp_existing:
        command.stamp(cfg, "0001")
        console.print("[green]Stamped[/green] schema at revision 0001.")
    else:
        command.upgrade(cfg, revision)
        console.print(f"[green]Upgraded[/green] to revision {revision}.")
