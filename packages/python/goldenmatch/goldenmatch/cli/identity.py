"""CLI: ``goldenmatch identity ...`` -- inspect and manage the Identity Graph."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goldenmatch.identity import (
    IdentityStore,
    customer_360_page,
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
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    dataset: str | None = typer.Option(None, "--dataset", help="Filter to identities in this dataset."),
    status: str | None = typer.Option(None, "--status", help="Filter to identities with this status."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of identities to return."),
    offset: int = typer.Option(0, "--offset", help="Number of identities to skip for pagination."),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON instead of a table."),
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
    entity_id: str = typer.Argument(..., help="The entity_id of the identity to operate on."),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON instead of a table."),
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


@identity_app.command("360")
def customer_360_cmd(
    entity_id: str = typer.Argument(..., help="The entity_id to build the Customer 360 view for."),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    no_relationships: bool = typer.Option(
        False, "--no-relationships", help="Skip the relationship neighborhood read."
    ),
    timeline_limit: int | None = typer.Option(
        None, "--timeline-limit", help="Cap the number of timeline events (most recent first)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the full 360 page as JSON."),
) -> None:
    """Customer 360: the unified serving view of one entity (golden record +
    provenance + linked records + timeline + relationships)."""
    with _open(path) as s:
        page = customer_360_page(
            s, entity_id,
            include_relationships=not no_relationships,
            timeline_limit=timeline_limit,
        )
    if page is None:
        err_console.print(f"[red]Not found:[/red] {entity_id}")
        raise typer.Exit(code=1)
    if json_out:
        console.print_json(json.dumps(page, default=str))
        return
    console.print(f"[bold cyan]{page['entity_id']}[/bold cyan]  status={page.get('status')}")
    console.print(
        f"  confidence: {page.get('confidence')}   records: {page.get('record_count')}   "
        f"conflicts: {page.get('conflict_count')}"
    )
    gr = page.get("golden_record") or {}
    if gr:
        t = Table(title="Golden record")
        t.add_column("field", style="cyan")
        t.add_column("value")
        for k, v in gr.items():
            t.add_row(str(k), "" if v is None else str(v))
        console.print(t)
    console.print(
        f"  sources: {', '.join(page.get('sources') or []) or '-'}   "
        f"timeline events: {len(page.get('timeline') or [])}   "
        f"relationships: {len(page.get('relationships') or [])}"
    )


@identity_app.command("certify-serving-joins")
def certify_serving_joins_cmd(
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    dataset: str | None = typer.Option(None, "--dataset", help="Restrict to one identity-graph dataset."),
    status: str = typer.Option("active", "--status", help="Entity status to include."),
    page_size: int = typer.Option(500, "--page-size", help="Entity-scan pagination size."),
    max_entities: int | None = typer.Option(
        None, "--max-entities", help="Cap the scan at this many entities (cert covers a prefix)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the certificate as JSON."),
) -> None:
    """Certify that a Customer 360 serving layer's source-record join key is
    unique — so a metric joined through the 360 provably can't double-count."""
    from goldenmatch.semantic import certify_serving_joins

    with _open(path) as s:
        cert = certify_serving_joins(
            s, dataset=dataset, status=status, page_size=page_size, max_entities=max_entities
        )
    rc = cert.record_certificate
    payload = {
        "trustworthy": cert.is_trustworthy,
        "n_entities": cert.n_entities,
        "n_records": cert.n_records,
        "truncated": cert.truncated,
        "record_id": {
            "is_unique_at_grain": rc.is_unique_at_grain,
            "duplicate_key_groups": rc.duplicate_key_groups,
            "max_fan_out": rc.max_fan_out,
            "estimate": rc.estimate,
        },
    }
    if json_out:
        console.print_json(json.dumps(payload))
        return
    verdict = "[green]TRUSTWORTHY[/green]" if cert.is_trustworthy else "[red]NOT trustworthy[/red]"
    console.print(f"Serving-join certificate: {verdict}")
    console.print(
        f"  entities: {cert.n_entities}   records: {cert.n_records}"
        f"   truncated: {cert.truncated}"
    )
    console.print(
        f"  record_id unique-at-grain: {rc.is_unique_at_grain}   "
        f"duplicate key groups: {rc.duplicate_key_groups}   "
        f"max fan-out: {rc.max_fan_out}   estimate: {rc.estimate:.4f}"
    )


@identity_app.command("emit-catalog")
def emit_catalog_cmd(
    source_name: str = typer.Argument(..., help="Logical source name the records were ingested under."),
    source_pk_column: str = typer.Argument(..., help="Column holding each record's source primary key."),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    dialect: str = typer.Option("metricflow", "--dialect", help="metricflow | cube | osi."),
    dataset: str | None = typer.Option(None, "--dataset", help="Identity-graph dataset scope."),
    source_target: str | None = typer.Option(
        None, "--source-target", help="Source model/cube/dataset the join points at (defaults to source_name)."
    ),
    resolved_key: str = typer.Option(
        "resolved_entity_id", "--resolved-key", help="The conformed join column name."
    ),
    out_path: str | None = typer.Option(
        None, "--out", help="Write the emitted YAML to this catalog file."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing catalog file at --out."),
) -> None:
    """Emit a conformed semantic-layer catalog (the ``resolved_entity_id`` join)
    directly from the durable identity store."""
    from goldenmatch.semantic import emit_semantic_model_from_store

    emit_kwargs: dict = {}
    if dataset is not None:
        emit_kwargs["dataset"] = dataset
    if source_target is not None:
        emit_kwargs["source_target"] = source_target
    with _open(path) as s:
        yaml_str = emit_semantic_model_from_store(
            s,
            source_name=source_name,
            source_pk_column=source_pk_column,
            dialect=dialect,
            resolved_key=resolved_key,
            path=out_path,
            overwrite=overwrite,
            **emit_kwargs,
        )
    if out_path:
        console.print(f"[green]Wrote[/green] {dialect} catalog to {out_path}")
    else:
        console.print(yaml_str)


@identity_app.command("resolve")
def resolve_cmd(
    record_id: str = typer.Argument(..., help="`{source}:{pk}` to look up"),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON instead of a table."),
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
    entity_id: str = typer.Argument(..., help="The entity_id of the identity to operate on."),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of events to return."),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON instead of a table."),
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
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    dataset: str | None = typer.Option(None, "--dataset", help="Filter to identities in this dataset."),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON instead of a table."),
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
    reason: str | None = typer.Option(None, "--reason", help="Optional reason recorded in the event log."),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
) -> None:
    """Manually merge two identities. Records from ``absorb`` move to ``keep``."""
    with _open(path) as s:
        out = manual_merge(s, keep, absorb, reason=reason)
    console.print(f"[green]Merged[/green] {absorb[:8]}... -> {keep[:8]}... at {out['at']}")


@identity_app.command("split")
def split_cmd(
    entity_id: str = typer.Argument(..., help="The entity_id of the identity to operate on."),
    record_ids: list[str] = typer.Argument(..., help="record_ids to move to a new identity"),
    reason: str | None = typer.Option(None, "--reason", help="Optional reason recorded in the event log."),
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
) -> None:
    """Manually split records off into a new identity."""
    with _open(path) as s:
        out = manual_split(s, entity_id, record_ids, reason=reason)
    console.print(f"[green]Split[/green] {len(out['moved'])} records -> new id {out['new_entity_id'][:8]}...")


@identity_app.command("migrate-ids")
def migrate_ids_cmd(
    path: str = typer.Option(DEFAULT_PATH, "--path", help="Path to the identity graph database."),
    dsn: str | None = typer.Option(None, "--dsn", envvar="GOLDENMATCH_IDENTITY_DSN", help="Database connection string for the identity store."),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="Report what would change; mutate nothing."),
) -> None:
    """Migrate persisted record ids from the legacy \":hash:\" scheme to \":h1:\"."""
    from goldenmatch.identity import migrate_record_ids
    if dsn:
        store = IdentityStore(backend="postgres", connection=dsn)
    else:
        store = _open(path)  # reuses the existing not-found guard
    try:
        rpt = migrate_record_ids(store, dry_run=dry_run)
    except NotImplementedError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2)
    finally:
        store.close()
    tag = "[dry-run] " if rpt.dry_run else ""
    console.print(
        f"{tag}scanned={rpt.scanned} rewritten={rpt.rewritten} merged={rpt.merged} "
        f"clashed_distinct_entity={rpt.clashed_distinct_entity} "
        f"kept_unfingerprintable={rpt.kept_unfingerprintable} "
        f"edges_repointed={rpt.edges_repointed}")


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
