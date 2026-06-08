"""``goldenanalysis`` CLI.

Phase 1 ships ``report``. ``trend`` and ``regressions`` are visible-but-honest
stubs — they require ``ReportHistory`` (Phase 2 / 0.2.0).
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="goldenanalysis",
    no_args_is_help=True,
    add_completion=False,
    help="Measure and report across the Golden Suite (read-only).",
)

_FUTURE = "available in goldenanalysis 0.2.0 (ReportHistory)"


@app.command()
def report(
    input: Path = typer.Argument(..., help="A .parquet/.csv frame, or a .json AnalysisReport to re-render."),
    analyzers: str = typer.Option("all", "--analyzers", help="Comma-separated analyzer names, or 'all'."),
    output_format: str = typer.Option("markdown", "--format", help="markdown | json"),
    out: Path | None = typer.Option(None, "--out", help="Write the report here instead of (also) printing."),
) -> None:
    """Analyze a frame (or re-render a saved report) and print a report."""
    from goldenanalysis import analyze
    from goldenanalysis.models import AnalysisReport

    suffix = input.suffix.lower()
    if suffix == ".json":
        rep = AnalysisReport.from_json(input.read_text(encoding="utf-8"))
    else:
        import polars as pl

        if suffix == ".parquet":
            df = pl.read_parquet(input)
        elif suffix == ".csv":
            df = pl.read_csv(input)
        else:
            typer.echo(f"unsupported input type: {input.suffix!r} (want .parquet/.csv/.json)", err=True)
            raise typer.Exit(2)
        names = None if analyzers == "all" else [a.strip() for a in analyzers.split(",") if a.strip()]
        rep = analyze(df, analyzers=names, dataset=input.stem)

    if output_format == "json":
        text = rep.to_json()
    elif output_format == "markdown":
        text = rep.to_markdown()
    else:
        typer.echo(f"unknown --format {output_format!r} (want markdown|json)", err=True)
        raise typer.Exit(2)

    if out is not None:
        out.write_text(text, encoding="utf-8")
    typer.echo(text)


@app.command()
def trend(
    metric: str = typer.Option(..., "--metric", help="Metric key to trend, e.g. cluster.singleton_ratio."),
    dataset: str = typer.Option(..., "--dataset"),
    history: Path = typer.Option(..., "--history", help="ReportHistory backend path."),
    last: int = typer.Option(30, "--last"),
) -> None:
    """Trend a metric over a run history. (stub)"""
    typer.echo(f"`trend` is {_FUTURE}.", err=True)
    raise typer.Exit(1)


@app.command()
def regressions(
    dataset: str = typer.Option(..., "--dataset"),
    history: Path = typer.Option(..., "--history", help="ReportHistory backend path."),
    baseline: str = typer.Option("rolling_median", "--baseline"),
) -> None:
    """Detect metric regressions vs a baseline. (stub)"""
    typer.echo(f"`regressions` is {_FUTURE}.", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
