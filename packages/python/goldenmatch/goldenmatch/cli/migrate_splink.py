"""CLI command: one-shot Splink migration -- convert, verify, and run.

``goldenmatch migrate-splink model.json data.csv`` collapses the whole
migration into a single command:

  1. convert the Splink model to a GoldenMatch config (+ imported EM weights);
  2. print a coverage scorecard;
  3. (default on, when ``splink`` is installed) verify the conversion
     reproduces Splink's clustering on a sample and print the agreement;
  4. run the dedupe on the data and write the canonical (golden) records.

It reuses ``from_splink`` (Task 11), ``splink_verify`` (auto-verify), and the
``dedupe_df`` pipeline -- no new engine logic, just the one-command front door.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

if TYPE_CHECKING:
    from goldenmatch.config.from_splink import SplinkConversion

console = Console()
err_console = Console(stderr=True)


def _write_output(golden: Any, output: str) -> int:
    """Write the golden (canonical) records to ``output`` (.parquet or .csv).

    Returns the row count written. ``golden`` is a pyarrow Table.
    """
    path = Path(output)
    rows = getattr(golden, "num_rows", 0)
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq

        pq.write_table(golden, path)
    else:
        from goldenmatch._api import _frame_write_csv

        _frame_write_csv(golden, path)
    return rows


def migrate_splink_cmd(
    input_path: str = typer.Argument(
        ..., help="Splink settings or trained-model JSON file"
    ),
    data: str = typer.Argument(..., help="Data to deduplicate (parquet/csv)"),
    output: str = typer.Option(
        "clusters.parquet",
        "--output",
        "-o",
        help="Where to write the deduplicated (golden) records (parquet/csv)",
    ),
    config_out: str | None = typer.Option(
        None,
        "--config-out",
        help="Also write the converted GoldenMatch YAML config here",
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help=(
            "Verify the conversion against a locally-installed Splink on a "
            "sample (default on; skipped with a notice if splink is absent)"
        ),
    ),
    verify_sample: int = typer.Option(
        5000, "--verify-sample", help="Rows to run through both engines for --verify"
    ),
    verify_threshold: float = typer.Option(
        0.5, "--verify-threshold", help="Splink match-probability cut for --verify"
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Fail on any lossy mapping (warnings), not just errors"
    ),
    id_column: str | None = typer.Option(
        None,
        "--id-column",
        help="Column holding each row's id (default: auto-detect unique_id/id/record_id)",
    ),
) -> None:
    """Convert a Splink model, verify it, and run the dedupe -- all in one command."""
    from goldenmatch.cli.import_splink import _run_verify, _write_pair
    from goldenmatch.config.from_splink import SplinkConversionError, from_splink

    # 1. Convert.
    try:
        conversion = from_splink(input_path, strict=strict)
    except SplinkConversionError as exc:
        err_console.print(f"[red]Splink conversion failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    cov = conversion.coverage
    style = "green" if (cov is not None and cov.is_complete) else "yellow"
    summary = cov.line() if cov is not None else conversion.report.summary()
    console.print(
        f"[{style}]Converted[/{style}] [cyan]{input_path}[/cyan] -- {summary}"
    )

    if config_out:
        # Persist the config (+ a sibling model JSON when the input was trained,
        # so the written config carries model_path and won't retrain on reuse).
        model_out = (
            f"{config_out}.model.json" if conversion.em_model is not None else None
        )
        _write_pair(conversion.config, config_out, conversion.em_model, model_out)
        console.print(f"Wrote config to [cyan]{config_out}[/cyan]")

    # 2. Verify (best-effort; prints an agreement table or a skip notice).
    if verify:
        _run_verify(
            input_path, conversion, data, verify_sample, verify_threshold
        )

    # 3. Run the dedupe on the full data and write the golden records.
    _run_and_write(conversion, data, output, id_column)


def _run_and_write(
    conversion: SplinkConversion, data: str, output: str, id_column: str | None
) -> None:
    from goldenmatch._api import dedupe_df
    from goldenmatch.config.splink_upgrade import _load_frame

    df = _load_frame(data)
    config = conversion.config
    em = conversion.em_model

    with tempfile.TemporaryDirectory() as tmp:
        # Inject the imported EM weights via a temp model_path so the run scores
        # with Splink's trained weights instead of re-fitting EM on the data.
        if em is not None:
            matchkeys = config.get_matchkeys()
            covered = set(em.match_weights)
            if matchkeys and all(
                f.field in covered for f in matchkeys[0].fields if f.field
            ):
                config = config.model_copy(deep=True)
                model_path = str(Path(tmp) / "em.json")
                em.save_json(model_path)
                config.get_matchkeys()[0].model_path = model_path
        result = dedupe_df(df, config=config, source_name="migrate_splink")

    written = _write_output(result.golden, output) if result.golden is not None else 0
    total = result.total_records
    unique_n = getattr(result.unique, "num_rows", 0) if result.unique is not None else 0
    entities = unique_n + written
    console.print(
        f"[green]Deduped[/green] {total} rows -> {entities} entities "
        f"({written} multi-record clusters). Wrote [cyan]{output}[/cyan]"
    )
