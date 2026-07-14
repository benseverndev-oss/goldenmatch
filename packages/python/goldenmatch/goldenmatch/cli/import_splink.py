"""CLI command: convert a Splink settings or trained-model JSON file into a
GoldenMatch config.

Spec: docs/superpowers/specs/2026-07-13-splink-config-converter-design.md
Wraps goldenmatch.config.from_splink.from_splink() -- see Task 11 for the
conversion logic itself. This module owns the CLI-only concerns: writing
the YAML, optionally persisting the trained EM model (--model-out), and
rendering the ConversionReport.

The `--upgrade` flag additionally runs the data-aware upgrade pass (spec:
docs/superpowers/specs/2026-07-14-splink-migration-upgrade-design.md) via
goldenmatch.config.splink_upgrade.upgrade_splink_conversion(). With
`--upgrade`, the faithful baseline conversion is written alongside the
upgraded config/model as an `*.baseline.*` pair so the trust anchor stays
on disk next to the tuned artifacts.
"""
from __future__ import annotations

from pathlib import Path

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


def _render_delta_table(measurement) -> Table:
    """Plain baseline -> upgraded delta table from a MeasurementResult.

    Rows are metric | baseline | upgraded, always covering cluster shape +
    wall time; vs_splink / vs_labels rows are added only when that
    reference was provided (they are ``None`` otherwise).
    """
    table = Table(title="Baseline -> Upgraded Delta", header_style="bold #d4a017")
    table.add_column("metric")
    table.add_column("baseline")
    table.add_column("upgraded")

    b, u = measurement.baseline, measurement.upgraded
    table.add_row("clusters", str(b.cluster_count), str(u.cluster_count))
    table.add_row("multi-record clusters", str(b.multi_record_clusters), str(u.multi_record_clusters))
    table.add_row("max cluster size", str(b.max_cluster_size), str(u.max_cluster_size))
    table.add_row("wall (s)", f"{b.wall_seconds:.3f}", f"{u.wall_seconds:.3f}")

    if measurement.vs_splink is not None:
        vb, vu = measurement.vs_splink.baseline, measurement.vs_splink.upgraded
        table.add_row("vs_splink F1", f"{vb['f1']:.3f}", f"{vu['f1']:.3f}")

    if measurement.vs_labels is not None:
        lb, lu = measurement.vs_labels.baseline, measurement.vs_labels.upgraded
        table.add_row("vs_labels F1", f"{lb['pairwise_f1']:.3f}", f"{lu['pairwise_f1']:.3f}")
        table.add_row("vs_labels b-cubed F1", f"{lb['bcubed_f1']:.3f}", f"{lu['bcubed_f1']:.3f}")

    return table


def _write_pair(config, yaml_path: str, em_model, model_out: str | None) -> bool:
    """Write one (config, trained-model) pair to disk. Returns True when the
    model was persisted (so the caller can report it).

    Mirrors the single-pair ordering/guard semantics used pre-`--upgrade`:
    set model_path in-memory first, write the YAML config, THEN persist the
    model -- a failed YAML write must not leave an orphaned model.json
    behind. Also refuses to persist a partial model (mixed bare/trained
    input covering only some matchkey fields) rather than shipping a
    config+model pair that would fail FS model validation at runtime.

    Silent on stdout (error messages still go to stderr before exiting) --
    the CALLERS own all success/warning output, so the non-`--upgrade` path
    can reproduce the pre-refactor stdout byte-for-byte.

    Raises `typer.Exit(1)` on any write/refusal failure -- callers that
    write multiple pairs (the `--upgrade` baseline-then-upgraded sequence)
    rely on this to guarantee a failure on pair N never runs pair N+1: the
    baseline is always written FIRST, so an upgraded-pair failure still
    leaves a complete, usable baseline pair on disk.
    """
    persist_model = em_model is not None and bool(model_out)

    missing_fields: list[str] = []
    if persist_model:
        covered = set(em_model.match_weights)
        missing_fields = [
            f.field
            for f in config.matchkeys[0].fields
            if f.field and f.field not in covered
        ]
        if missing_fields:
            persist_model = False

    if persist_model:
        config.matchkeys[0].model_path = model_out

    dumped = config.model_dump(exclude_none=True, exclude_defaults=True)
    try:
        with open(yaml_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(dumped, fh, sort_keys=False)
    except OSError as exc:
        err_console.print(
            f"[red]Could not write config to[/red] [cyan]{yaml_path}[/cyan]: {exc}"
        )
        raise typer.Exit(code=1) from None

    if missing_fields:
        err_console.print(
            "[red]--model-out refused:[/red] the imported Splink model does "
            f"not cover field(s) [bold]{', '.join(missing_fields)}[/bold] of "
            "matchkeys[0] (mixed bare/trained input). A partial model would "
            "fail FS model validation at runtime. The config was written to "
            f"[cyan]{yaml_path}[/cyan] WITHOUT model_path; it will re-train via "
            "EM on first run. No model file was written."
        )
        raise typer.Exit(code=1)

    if persist_model:
        # save_json creates parent dirs itself (os.makedirs), but can still
        # hit permission errors or invalid path components.
        try:
            em_model.save_json(model_out)
        except OSError as exc:
            err_console.print(
                f"[red]Could not write trained model to[/red] "
                f"[cyan]{model_out}[/cyan]: {exc}. Note: the config written to "
                f"[cyan]{yaml_path}[/cyan] references this model via "
                "matchkeys[0].model_path, but the model file failed to write."
            )
            raise typer.Exit(code=1) from None

    return persist_model


def _baseline_path(path: str) -> str:
    """`out.yaml` -> `out.baseline.yaml`; `model.json` -> `model.baseline.json`."""
    p = Path(path)
    return str(p.with_name(f"{p.stem}.baseline{p.suffix}"))


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
    upgrade: str | None = typer.Option(
        None,
        "--upgrade",
        help=(
            "Run the data-aware upgrade pass against this dataset (parquet/csv): "
            "four levers -- tf_tables, distance_thresholds, fan_out (negative "
            "evidence + cluster-guard tuning), calibration. "
            "Writes the UPGRADED config/model to --output/--model-out and the "
            "faithful baseline alongside as out.baseline.yaml/model.baseline.json."
        ),
    ),
    splink_clusters: str | None = typer.Option(
        None,
        "--splink-clusters",
        help=(
            "Optional reference cluster mapping (parquet/csv) from the prior "
            "Splink run, for agreement measurement. First column = id, "
            "second column = cluster_id."
        ),
    ),
    labels: str | None = typer.Option(
        None,
        "--labels",
        help=(
            "Optional ground-truth cluster mapping (parquet/csv) for true "
            "pairwise + B-cubed F1 measurement. First column = id, second "
            "column = cluster_id."
        ),
    ),
    sample_cap: int = typer.Option(
        100_000,
        "--sample-cap",
        help="Row cap for --upgrade lever computation and measurement (seeded subsample above it)",
    ),
    no_measure: bool = typer.Option(
        False, "--no-measure", help="Skip the baseline-vs-upgraded measurement pass"
    ),
    id_column: str | None = typer.Option(
        None,
        "--id-column",
        help=(
            "Column holding each row's id for --upgrade measurement/reference "
            "joins (default: auto-detect unique_id/id/record_id)"
        ),
    ),
) -> None:
    """Convert a Splink settings (or trained-model) JSON file into a GoldenMatch YAML config."""
    from goldenmatch.config.from_splink import SplinkConversionError, from_splink

    try:
        conversion = from_splink(input_path, strict=strict)
    except SplinkConversionError as exc:
        err_console.print(f"[red]Splink conversion failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    if upgrade is None:
        # Existing behavior: stdout is byte-identical to the pre---upgrade
        # implementation (warning, persisted message, findings table, then
        # ONE combined "Wrote config to <path>. <summary>" line).
        if conversion.em_model is not None and not model_out:
            console.print(
                "[yellow]Warning:[/yellow] the Splink input carried trained m/u "
                "probabilities, but they were NOT persisted -- pass "
                "[bold]--model-out[/bold] <path> to keep them. The output "
                "config will re-train via EM on first run instead."
            )

        persisted = _write_pair(conversion.config, output, conversion.em_model, model_out)
        if persisted:
            console.print(
                f"[green]Trained model persisted to[/green] [cyan]{model_out}[/cyan] "
                f"(set as matchkeys[0].model_path)."
            )

        table = _render_report_table(conversion.report.findings)
        if table is not None:
            console.print(table)
        console.print(f"Wrote config to [cyan]{output}[/cyan]. {conversion.report.summary()}")
        return

    # --upgrade: a trained-model input needs --model-out to persist the
    # upgraded model (TF tables etc.) -- refuse cheaply, before running the
    # (potentially expensive) upgrade pass.
    if conversion.em_model is not None and not model_out:
        err_console.print(
            "[red]--upgrade refused:[/red] the Splink input carried trained "
            "m/u probabilities, so the upgrade pass needs [bold]--model-out[/bold] "
            "<path> to persist the upgraded model. Pass --model-out or drop --upgrade."
        )
        raise typer.Exit(code=1)

    from goldenmatch.config.splink_upgrade import SplinkUpgradeError, upgrade_splink_conversion

    try:
        result = upgrade_splink_conversion(
            conversion,
            upgrade,
            sample_cap=sample_cap,
            splink_clusters=splink_clusters,
            labels=labels,
            measure=not no_measure,
            id_column=id_column,
        )
    except SplinkUpgradeError as exc:
        err_console.print(f"[red]Splink upgrade failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    baseline_yaml = _baseline_path(output)
    baseline_model = _baseline_path(model_out) if model_out else None

    # Baseline pair FIRST -- the trust anchor is always on disk before the
    # upgraded pair is attempted, so a failure writing the upgraded pair
    # never leaves an upgraded config without its baseline.
    baseline_persisted = _write_pair(
        result.baseline_config, baseline_yaml, conversion.em_model, baseline_model
    )
    upgraded_persisted = _write_pair(result.upgraded_config, output, result.em_model, model_out)

    table = _render_report_table(result.report.findings)
    if table is not None:
        console.print(table)
    console.print(result.report.summary())
    console.print(
        f"Wrote baseline config to [cyan]{baseline_yaml}[/cyan]"
        + (f" (model: [cyan]{baseline_model}[/cyan])" if baseline_persisted else "")
        + "."
    )
    console.print(
        f"Wrote upgraded config to [cyan]{output}[/cyan]"
        + (f" (model: [cyan]{model_out}[/cyan])" if upgraded_persisted else "")
        + "."
    )

    if result.measurement is not None:
        console.print(_render_delta_table(result.measurement))
