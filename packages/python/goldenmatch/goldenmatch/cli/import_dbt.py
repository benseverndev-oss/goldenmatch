"""CLI command: distill a hand-rolled dbt project's ER logic into a config.

Spec: docs/superpowers/specs/2026-07-26-dbt-to-goldenmatch-converter-design.md

Wraps goldenmatch.config.from_dbt.from_dbt(): reads a dbt ``manifest.json``,
identifies the entity-resolution models, extracts the recognizable ER idioms
into ONE GoldenMatchConfig, and prints a coverage scorecard + the findings
(including the ``couldn't extract`` list for human review).

The optional ``--verify`` flag proves the distillation reproduces the dbt
pipeline's EXISTING output: on a sample of the source rows it runs the converted
config and reports pairwise cluster agreement against the provided output table
(goldenmatch.config.dbt_verify.verify_against_dbt) -- a one-command "reproduces
N% of your existing clusters." Best-effort: it degrades to a skip notice when
the output table is empty or shares no ids with the source.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import typer
import yaml
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from goldenmatch.config.dbt_verify import DbtVerification
    from goldenmatch.config.from_dbt import DbtConversion
    from goldenmatch.config.from_splink import ConversionFinding

console = Console()
err_console = Console(stderr=True)


def _render_report_table(findings: list[ConversionFinding]) -> Table | None:
    if not findings:
        return None
    table = Table(title="dbt Conversion Findings", header_style="bold #d4a017")
    table.add_column("Severity")
    table.add_column("Location")
    table.add_column("Message")
    table.add_column("Mapped To")
    severity_style = {"error": "red", "warning": "yellow", "info": "dim"}
    for f in findings:
        style = severity_style.get(f.severity, "")
        table.add_row(
            f"[{style}]{f.severity}[/{style}]" if style else f.severity,
            f.splink_path,
            f.message,
            f.mapped_to or "",
        )
    return table


def _render_verify_table(verification: DbtVerification) -> Table:
    a = verification.agreement
    verdict = (
        "[green]faithful[/green]"
        if verification.is_faithful
        else "[yellow]divergent[/yellow]"
    )
    table = Table(
        title=f"dbt agreement ({verdict})", header_style="bold #d4a017",
    )
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("reproduces existing clusters", f"{a['f1'] * 100:.1f}%")
    table.add_row("pairwise F1", f"{a['f1']:.3f}")
    table.add_row("pairwise precision", f"{a['precision']:.3f}")
    table.add_row("pairwise recall", f"{a['recall']:.3f}")
    table.add_row("records compared", str(verification.n_shared_ids))
    table.add_row(
        "multi-record clusters (GM / dbt)",
        f"{verification.gm_multi_clusters} / {verification.dbt_multi_clusters}",
    )
    return table


def _run_verify(
    conversion: DbtConversion,
    source: str,
    output_table: str,
    sample: int,
    id_column: str | None,
) -> None:
    """Run the auto-verify pass and print a table (or a skip notice)."""
    from goldenmatch.config.dbt_verify import verify_against_dbt

    if conversion.config is None:
        console.print(
            "[dim]dbt verification skipped: no config was extracted.[/dim]"
        )
        return
    result = verify_against_dbt(
        conversion.config,
        source,
        output_table,
        id_column=id_column,
        sample_size=sample,
        report=conversion.report,
    )
    if result is None:
        console.print(
            "[dim]dbt verification skipped (see findings for the reason).[/dim]"
        )
        return
    console.print(_render_verify_table(result))


def import_dbt_cmd(
    manifest: str = typer.Argument(
        ..., help="dbt manifest.json (from `dbt compile` / `dbt parse`)"
    ),
    output: str = typer.Option(
        "goldenmatch.yaml", "--output", "-o", help="Output YAML config path"
    ),
    catalog: str | None = typer.Option(
        None,
        "--catalog",
        help=(
            "dbt catalog.json (from `dbt docs generate`); supplies model column "
            "lists so recognized most-recent survivorship is emitted as "
            "golden_rules (otherwise it is only reported)"
        ),
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Fail on any lossy finding (warnings), not just errors"
    ),
    min_confidence: float = typer.Option(
        0.5,
        "--min-confidence",
        help="ER-model identification confidence floor for signal extraction",
    ),
    select: list[str] = typer.Option(
        [],
        "--select",
        "-s",
        help=(
            "Scope to specific ER models (repeatable), dbt-style; without it a full "
            "warehouse over-extracts. Globs the model name (`dedup_*`), or "
            "`path:*entity_resolution*`, or `tag:<name>`. Selected models bypass "
            "--min-confidence."
        ),
    ),
    verify: str | None = typer.Option(
        None,
        "--verify",
        help=(
            "Prove the distillation reproduces your dbt pipeline: run the "
            "converted config on a sample of --source and report pairwise "
            "cluster agreement against this output table (parquet/csv; first "
            "two columns = id, cluster_id). Needs --source."
        ),
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Source rows the dbt ER model consumed (parquet/csv), for --verify",
    ),
    verify_sample: int = typer.Option(
        5000,
        "--verify-sample",
        help="Rows of --source to run through GoldenMatch for --verify",
    ),
    id_column: str | None = typer.Option(
        None,
        "--id-column",
        help="Column holding each source row's id (default: auto-detect unique_id/id/record_id)",
    ),
) -> None:
    """Distill a hand-rolled dbt project's ER logic into a GoldenMatch config."""
    from goldenmatch.config.from_dbt import DbtConversionError, from_dbt

    try:
        conversion = from_dbt(
            manifest, catalog_path=catalog, strict=strict, min_confidence=min_confidence,
            select=select or None,
        )
    except DbtConversionError as exc:
        err_console.print(f"[red]dbt conversion failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    cov = conversion.coverage
    style = "green" if cov.has_config else "yellow"
    console.print(f"[{style}]Distilled[/{style}] [cyan]{manifest}[/cyan] -- {cov.line()}")

    if conversion.config is not None:
        dumped = conversion.config.model_dump(exclude_none=True, exclude_defaults=True)
        try:
            with open(output, "w", encoding="utf-8") as fh:
                yaml.safe_dump(dumped, fh, sort_keys=False)
        except OSError as exc:
            err_console.print(
                f"[red]Could not write config to[/red] [cyan]{output}[/cyan]: {exc}"
            )
            raise typer.Exit(code=1) from None
        console.print(f"Wrote config to [cyan]{output}[/cyan].")
    else:
        console.print(
            "[yellow]No config written[/yellow] -- no entity-resolution logic was "
            "recognized in this project."
        )

    if verify is not None:
        if source is None:
            err_console.print(
                "[red]--verify needs --source[/red] (the rows the dbt model consumed)."
            )
            raise typer.Exit(code=1)
        _run_verify(conversion, source, verify, verify_sample, id_column)

    table = _render_report_table(conversion.report.findings)
    if table is not None:
        console.print(table)
    console.print(conversion.report.summary())
