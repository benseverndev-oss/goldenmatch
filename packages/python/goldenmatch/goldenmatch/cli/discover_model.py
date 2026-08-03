"""CLI `discover-model` command — the semantic-model discovery front door.

Surfaces ``goldenmatch.semantic.discover_semantic_model``: point it at a set of source
tables and get a DRAFT semantic model (MetricFlow) where every key is PRE-GRADED by the
certifier — grain, entity types, a certified join graph, and grain-gated measures. It
proposes + proves; a human reviews and approves. Advisory only; nothing auto-ships.
Mirrors `certify-keys`.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def discover_model_cmd(
    data: list[str] = typer.Option(
        ..., "--data", "-d",
        help="A source table as name=path (repeatable), e.g. -d orders=orders.csv",
    ),
    dialect: str = typer.Option(
        "metricflow", "--dialect",
        help="Emit dialect for the draft model: metricflow / cube / osi.",
    ),
    output: str | None = typer.Option(
        None, "--output", "-o",
        help="Write the draft semantic-model YAML to this path.",
    ),
    resolve: bool = typer.Option(
        False, "--resolve",
        help="Also measure entity fragmentation / undercount via ER (fail-open).",
    ),
    name: bool = typer.Option(
        False, "--name",
        help="Also run the OPTIONAL advisory LLM namer (business names for "
             "entities/dimensions/values/measures; abstains without an LLM key).",
    ),
    apply_names: bool = typer.Option(
        False, "--apply-names",
        help="Write the namer's VERIFIED names into the emitted YAML "
             "(entity/measure label:, dimension/value meta.goldenmatch.glossary). "
             "Implies --name; post-certification + cosmetic.",
    ),
    fail_untrustworthy: bool = typer.Option(
        False, "--fail-untrustworthy",
        help="Exit non-zero if any proposed key is not unique at grain (CI gate).",
    ),
    as_json: bool = typer.Option(
        False, "--json",
        help="Emit the full proposed model (shape + certification) as JSON.",
    ),
) -> None:
    """Discover a draft semantic model from source tables, every key pre-graded."""
    import json

    from goldenmatch.core.io_arrow import read_table_arrow
    from goldenmatch.semantic import discover_semantic_model

    tables: dict[str, object] = {}
    for spec in data:
        if "=" not in spec:
            console.print(f"[red]--data must be name=path, got {spec!r}[/red]")
            raise typer.Exit(code=2)
        tbl_name, path = spec.split("=", 1)
        tables[tbl_name.strip()] = read_table_arrow(path.strip())

    try:
        model = discover_semantic_model(
            tables, dialect=dialect, resolve=resolve, name=name, apply_names=apply_names
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(model.yaml)

    if as_json:
        print(json.dumps(model.to_dict(), indent=2))
    else:
        console.print(
            f"[bold]Dialect:[/bold] {model.dialect}   "
            f"[bold]tables:[/bold] {len(model.tables)}   "
            f"[bold]entity types:[/bold] {len(model.entity_types)}   "
            f"[bold]joins:[/bold] {len(model.joins)}   "
            f"[bold]all_trustworthy:[/bold] "
            f"{'[#2ecc71]yes[/]' if model.all_trustworthy else '[red]no[/]'}"
        )
        if model.tables:
            tbl = Table(show_header=True, header_style="bold")
            tbl.add_column("table")
            tbl.add_column("entity")
            tbl.add_column("grain")
            tbl.add_column("verdict")
            tbl.add_column("measures", justify="right")
            tbl.add_column("dimensions", justify="right")
            for pt in model.tables:
                ok = pt.grain_trustworthy
                tbl.add_row(
                    pt.table,
                    pt.entity_type or "-",
                    ", ".join(pt.grain) or "-",
                    "[#2ecc71]trustworthy[/]" if ok else "[red]UNTRUSTWORTHY[/]",
                    str(len(pt.measures)),
                    str(len(pt.dimensions)),
                )
            console.print(tbl)
        if model.joins:
            console.print("[bold]Joins[/bold]")
            for j in model.joins:
                arrow = "[#2ecc71]->[/]" if j.is_trustworthy else "[red]-x[/]"
                console.print(
                    f"  {j.from_table}.{j.from_column} {arrow} "
                    f"{j.to_table}.{j.to_column} ({j.relationship})"
                )
        if output:
            console.print(f"[dim]wrote {output}[/dim]")

    if fail_untrustworthy and not model.all_trustworthy:
        if not as_json:
            console.print("[red]one or more proposed keys are untrustworthy for metric use.[/red]")
        raise typer.Exit(code=1)
