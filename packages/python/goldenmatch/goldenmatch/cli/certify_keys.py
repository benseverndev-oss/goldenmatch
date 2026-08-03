"""CLI `certify-keys` command -- the semantic-layer front door.

Surfaces ``goldenmatch.semantic.certify_semantic_model``: point it at a
dbt/MetricFlow, Cube, or OSI/Ossie semantic model plus the data behind each
model and get an advisory key-integrity certificate (uniqueness at grain +
measure fan-out) for every declared entity key -- the keys a metric silently
joins on. Advisory only; it never mutates a metric.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def certify_keys_cmd(
    model: str = typer.Argument(..., help="Semantic-model file (dbt/MetricFlow, Cube, or OSI YAML)"),
    data: list[str] = typer.Option(
        ..., "--data", "-d",
        help="Frame for a model/dataset/cube as name=path (repeatable), e.g. -d orders=orders.csv",
    ),
    resolve: bool = typer.Option(
        False, "--resolve",
        help="Also measure entity fragmentation / undercount via ER (MetricFlow dialect only).",
    ),
    fail_untrustworthy: bool = typer.Option(
        False, "--fail-untrustworthy",
        help="Exit non-zero if any declared key is not unique at grain (CI gate).",
    ),
    as_json: bool = typer.Option(
        False, "--json",
        help="Emit the verdict-rich report as JSON (the shared MCP/REST shape) — "
             "machine-readable for a CI gate.",
    ),
) -> None:
    """Certify every declared key in a semantic model against its data."""
    import json

    from goldenmatch.core.io_arrow import read_table_arrow
    from goldenmatch.semantic import certification_report_dict, certify_semantic_model

    frames: dict[str, object] = {}
    for spec in data:
        if "=" not in spec:
            console.print(f"[red]--data must be name=path, got {spec!r}[/red]")
            raise typer.Exit(code=2)
        name, path = spec.split("=", 1)
        frames[name.strip()] = read_table_arrow(path.strip())

    report = certify_semantic_model(model, frames, resolve=resolve)

    if as_json:
        # Plain print (not the rich console) so the JSON is clean on stdout.
        print(json.dumps(certification_report_dict(report), indent=2))
    else:
        console.print(f"[bold]Dialect:[/bold] {report.dialect}   "
                      f"[bold]certified:[/bold] {report.n_certified}   "
                      f"[bold]untrustworthy:[/bold] {len(report.untrustworthy)}")
        if report.skipped:
            console.print(f"[dim]skipped (no frame supplied): {', '.join(report.skipped)}[/dim]")
        if report.note:
            console.print(f"[dim]{report.note}[/dim]")

        if report.entries:
            show_undercount = any(
                e.certificate.undercount_estimate is not None for e in report.entries
            )
            table = Table(show_header=True, header_style="bold")
            table.add_column("target")
            table.add_column("key")
            table.add_column("verdict")
            table.add_column("max_fan_out", justify="right")
            if show_undercount:
                table.add_column("undercount", justify="right")
            table.add_column("context", style="dim")
            for e in report.entries:
                cert = e.certificate
                ok = cert.is_trustworthy()
                row = [
                    e.target,
                    ", ".join(e.key),
                    "[#2ecc71]trustworthy[/]" if ok else "[red]UNTRUSTWORTHY[/]",
                    f"{cert.max_fan_out:g}",
                ]
                if show_undercount:
                    uc = cert.undercount_estimate
                    row.append("-" if uc is None else f"{uc:.2f}")
                row.append(e.context)
                table.add_row(*row)
            console.print(table)

    if fail_untrustworthy and report.untrustworthy:
        n = len(report.untrustworthy)
        if not as_json:
            console.print(f"[red]{n} declared key(s) are untrustworthy for metric use.[/red]")
        raise typer.Exit(code=1)
