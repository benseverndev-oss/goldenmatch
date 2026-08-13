"""CLI `ontology` command group — the ontology-layer (RDF/OWL/SHACL) front door.

Surfaces the `goldenmatch.semantic.ontology` capabilities on the command line,
mirroring `certify-keys` / `discover-model`:

- `ontology certify <ontology.ttl> --data Class=path...` — certify the identity
  keys (`owl:hasKey` / IFP) an ontology declares against real instance data;
- `ontology discover --data Class=path...` — discover a DRAFT OWL ontology whose
  `owl:hasKey` is chosen and PRE-GRADED by the certifier.

Advisory only; nothing auto-ships. `rdflib` is optional (`goldenmatch[ontology]`);
without it the commands exit with a clear install hint.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()

ontology_app = typer.Typer(
    help="Ontology-layer (RDF/OWL/SHACL) identity front door — certify + discover.",
    no_args_is_help=True,
)


def _read_frames(data: list[str]) -> dict[str, object]:
    """Parse repeated `Class=path` options into `{class: arrow table}`."""
    from goldenmatch.core.io_arrow import read_table_arrow

    frames: dict[str, object] = {}
    for spec in data:
        if "=" not in spec:
            console.print(f"[red]--data must be Class=path, got {spec!r}[/red]")
            raise typer.Exit(code=2)
        cls, path = spec.split("=", 1)
        frames[cls.strip()] = read_table_arrow(path.strip())
    return frames


@ontology_app.command("certify")
def certify_cmd(
    ontology: str = typer.Argument(
        ..., help="OWL/RDF ontology file (Turtle / RDF-XML / JSON-LD / N-Triples).",
    ),
    data: list[str] = typer.Option(
        ..., "--data", "-d",
        help="Instance data for a class as Class=path (repeatable), e.g. -d Patient=patients.csv",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the certification report as JSON."),
    fail_untrustworthy: bool = typer.Option(
        False, "--fail-untrustworthy",
        help="Exit non-zero if any declared identity key is not unique at grain (CI gate).",
    ),
) -> None:
    """Certify the identity keys an ontology declares against instance data."""
    import json

    from goldenmatch.semantic import certify_ontology

    frames = _read_frames(data)
    try:
        report = certify_ontology(ontology, frames)
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=3) from exc

    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        verdict = "[#2ecc71]all safe[/]" if report.all_safe else "[red]UNSAFE[/]"
        console.print(
            f"[bold]Ontology:[/bold] {report.ontology_iri or '-'}   "
            f"[bold]keys:[/bold] {report.n_keys}   "
            f"[bold]unsafe:[/bold] {report.n_unsafe}   {verdict}"
        )
        if report.keys:
            tbl = Table(show_header=True, header_style="bold")
            tbl.add_column("class")
            tbl.add_column("key")
            tbl.add_column("source")
            tbl.add_column("uniqueness", justify="right")
            tbl.add_column("verdict")
            for k in report.keys:
                tbl.add_row(
                    str(k["class"]), ", ".join(k["key"]), k["source"],
                    f"{k['estimate']:.3f}",
                    "[#2ecc71]unique[/]" if k["is_unique"] else "[red]NOT UNIQUE[/]",
                )
            console.print(tbl)
        if report.note:
            console.print(f"[dim]{report.note}[/dim]")

    if fail_untrustworthy and not report.all_safe:
        raise typer.Exit(code=1)


@ontology_app.command("discover")
def discover_cmd(
    data: list[str] = typer.Option(
        ..., "--data", "-d",
        help="A class's instance data as Class=path (repeatable), e.g. -d Customer=customers.csv",
    ),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Write the draft OWL ontology (Turtle) to this path.",
    ),
    base_iri: str | None = typer.Option(None, "--base-iri", help="Base IRI for the emitted terms."),
    ontology_iri: str | None = typer.Option(None, "--ontology-iri", help="IRI for the ontology node."),
    as_json: bool = typer.Option(False, "--json", help="Emit the discovery result as JSON."),
    fail_untrustworthy: bool = typer.Option(
        False, "--fail-untrustworthy",
        help="Exit non-zero if any discovered class key is not unique at grain (CI gate).",
    ),
) -> None:
    """Discover a draft OWL ontology from data, every owl:hasKey pre-graded."""
    import json

    from goldenmatch.semantic import discover_ontology
    from goldenmatch.semantic.ontology import DEFAULT_BASE_IRI

    frames = _read_frames(data)
    try:
        disc = discover_ontology(
            frames, base_iri=base_iri or DEFAULT_BASE_IRI, ontology_iri=ontology_iri,
        )
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=3) from exc

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            fh.write(disc.turtle)

    all_trustworthy = all(c["is_trustworthy"] for c in disc.classes) if disc.classes else True
    if as_json:
        print(json.dumps(disc.to_dict(), indent=2))
    else:
        console.print(
            f"[bold]Ontology:[/bold] {disc.ontology_iri or '-'}   "
            f"[bold]classes:[/bold] {len(disc.classes)}   "
            f"[bold]all_trustworthy:[/bold] "
            f"{'[#2ecc71]yes[/]' if all_trustworthy else '[red]no[/]'}"
        )
        if disc.classes:
            tbl = Table(show_header=True, header_style="bold")
            tbl.add_column("class")
            tbl.add_column("owl:hasKey")
            tbl.add_column("uniqueness", justify="right")
            tbl.add_column("verdict")
            for c in disc.classes:
                tbl.add_row(
                    c["class"], ", ".join(c["key"]), f"{c['estimate']:.3f}",
                    "[#2ecc71]trustworthy[/]" if c["is_trustworthy"] else "[red]UNTRUSTWORTHY[/]",
                )
            console.print(tbl)
        if output:
            console.print(f"[dim]wrote {output}[/dim]")

    if fail_untrustworthy and not all_trustworthy:
        raise typer.Exit(code=1)
