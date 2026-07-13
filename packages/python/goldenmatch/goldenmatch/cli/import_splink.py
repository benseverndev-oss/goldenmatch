"""CLI command: convert a Splink settings or trained-model JSON file into a
GoldenMatch config.

Spec: docs/superpowers/specs/2026-07-13-splink-config-converter-design.md
Wraps goldenmatch.config.from_splink.from_splink() -- see Task 11 for the
conversion logic itself. This module owns the CLI-only concerns: writing
the YAML, optionally persisting the trained EM model (--model-out), and
rendering the ConversionReport.
"""
from __future__ import annotations

import typer
import yaml
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def _render_report_table(findings) -> Table | None:
    if not findings:
        return None
    table = Table(title="Splink Conversion Findings", header_style="bold #d4a017")
    table.add_column("Severity")
    table.add_column("Splink Path")
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


def import_splink_cmd(
    input_path: str = typer.Argument(..., help="Splink settings or trained-model JSON file"),
    output: str = typer.Option(
        "goldenmatch.yaml", "--output", "-o", help="Output YAML config path"
    ),
    model_out: str | None = typer.Option(
        None,
        "--model-out",
        help="Persist imported trained m/u as an FS model JSON; sets model_path in the config",
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Fail on any lossy mapping (warnings), not just errors"
    ),
) -> None:
    """Convert a Splink settings (or trained-model) JSON file into a GoldenMatch YAML config."""
    from goldenmatch.config.from_splink import SplinkConversionError, from_splink

    try:
        conversion = from_splink(input_path, strict=strict)
    except SplinkConversionError as exc:
        err_console.print(f"[red]Splink conversion failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    # Ordering: set model_path in-memory first, write the YAML config, THEN
    # persist the model -- a failed YAML write must not leave an orphaned
    # model.json behind.
    persist_model = conversion.em_model is not None and bool(model_out)

    # Partial-model guard: mixed bare/trained Splink input yields a model
    # that does not cover every converted field. Shipping that config+model
    # pair would fail at runtime with a misleading FSModelMismatchError
    # ("matchkey changed since training"), so refuse --model-out instead:
    # the config is still written, WITHOUT model_path (re-trains via EM).
    missing_fields: list[str] = []
    if persist_model:
        covered = set(conversion.em_model.match_weights)
        missing_fields = [
            f.field
            for f in conversion.config.matchkeys[0].fields
            if f.field and f.field not in covered
        ]
        if missing_fields:
            persist_model = False

    if conversion.em_model is not None:
        if persist_model:
            conversion.config.matchkeys[0].model_path = model_out
        elif not model_out:
            console.print(
                "[yellow]Warning:[/yellow] the Splink input carried trained m/u "
                "probabilities, but they were NOT persisted -- pass "
                "[bold]--model-out[/bold] <path> to keep them. The output "
                "config will re-train via EM on first run instead."
            )

    dumped = conversion.config.model_dump(exclude_none=True, exclude_defaults=True)
    try:
        with open(output, "w", encoding="utf-8") as fh:
            yaml.safe_dump(dumped, fh, sort_keys=False)
    except OSError as exc:
        err_console.print(
            f"[red]Could not write config to[/red] [cyan]{output}[/cyan]: {exc}"
        )
        raise typer.Exit(code=1) from None

    if missing_fields:
        err_console.print(
            "[red]--model-out refused:[/red] the imported Splink model does "
            f"not cover field(s) [bold]{', '.join(missing_fields)}[/bold] of "
            "matchkeys[0] (mixed bare/trained input). A partial model would "
            "fail FS model validation at runtime. The config was written to "
            f"[cyan]{output}[/cyan] WITHOUT model_path; it will re-train via "
            "EM on first run. No model file was written."
        )
        raise typer.Exit(code=1)

    if persist_model:
        # save_json creates parent dirs itself (os.makedirs), but can still
        # hit permission errors or invalid path components.
        try:
            conversion.em_model.save_json(model_out)
        except OSError as exc:
            err_console.print(
                f"[red]Could not write trained model to[/red] "
                f"[cyan]{model_out}[/cyan]: {exc}. Note: the config written to "
                f"[cyan]{output}[/cyan] references this model via "
                "matchkeys[0].model_path, but the model file failed to write."
            )
            raise typer.Exit(code=1) from None
        console.print(
            f"[green]Trained model persisted to[/green] [cyan]{model_out}[/cyan] "
            f"(set as matchkeys[0].model_path)."
        )

    table = _render_report_table(conversion.report.findings)
    if table is not None:
        console.print(table)
    console.print(f"Wrote config to [cyan]{output}[/cyan]. {conversion.report.summary()}")
