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


def _open_history(history: Path):
    """Open a ReportHistory, inferring the backend from the path suffix."""
    from goldenanalysis.history import ReportHistory

    backend = "sqlite" if history.suffix.lower() in (".db", ".sqlite") else "jsonl"
    return ReportHistory(backend=backend, path=history)


def _parse_policy(spec: str | None):
    """Parse ``--policy`` (JSON object or ``key=pct,key=pct``) into a RegressionPolicy."""
    from goldenanalysis.models import RegressionPolicy

    if not spec:
        return RegressionPolicy()
    spec = spec.strip()
    if spec.startswith("{"):
        import json

        data = json.loads(spec)
        return RegressionPolicy(
            default_pct=data.get("default_pct", 10.0), per_metric=data.get("per_metric", {})
        )
    per_metric = {}
    for pair in spec.split(","):
        if "=" in pair:
            key, val = pair.split("=", 1)
            per_metric[key.strip()] = float(val)
    return RegressionPolicy(per_metric=per_metric)


@app.command()
def trend(
    metric: str = typer.Option(..., "--metric", help="Metric key to trend, e.g. cluster.singleton_ratio."),
    dataset: str = typer.Option(..., "--dataset"),
    history: Path = typer.Option(..., "--history", help="ReportHistory path (.jsonl or .db)."),
    last: int = typer.Option(30, "--last"),
) -> None:
    """Trend a metric over a run history."""
    series = _open_history(history).trend(metric, dataset, last_n=last)
    if not series.points:
        typer.echo(f"no history for {metric!r} on dataset {dataset!r}")
        return
    typer.echo(f"# {metric} — {dataset} (last {len(series.points)})")
    typer.echo("| run_id | value |")
    typer.echo("|---|---|")
    for run_id, value in series.points:
        typer.echo(f"| {run_id} | {value:g} |")


@app.command()
def regressions(
    dataset: str = typer.Option(..., "--dataset"),
    history: Path = typer.Option(..., "--history", help="ReportHistory path (.jsonl or .db)."),
    baseline: str = typer.Option("rolling_median", "--baseline"),
    window: int = typer.Option(7, "--window"),
    policy: str | None = typer.Option(None, "--policy", help='Per-metric gates: JSON or "key=pct,key=pct".'),
    fail_on_regression: bool = typer.Option(
        False, "--fail-on-regression", help="Exit 1 if any regression is flagged (CI gate)."
    ),
) -> None:
    """Detect metric regressions vs a baseline."""
    flagged = _open_history(history).detect_regressions(
        dataset, baseline=baseline, window=window, policy=_parse_policy(policy)
    )
    if not flagged:
        typer.echo(f"no regressions on dataset {dataset!r}")
        return
    typer.echo(f"# {len(flagged)} regression(s) — {dataset}")
    typer.echo("| metric | baseline | current | delta |")
    typer.echo("|---|---|---|---|")
    for r in flagged:
        typer.echo(f"| {r.metric} | {r.baseline:g} | {r.current:g} | {r.delta_pct:+.1f}% |")
    if fail_on_regression:
        raise typer.Exit(1)


@app.command(name="mcp-serve")
def mcp_serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio | http"),
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8300, "--port", help="HTTP port (A2A convention: Analysis = 8300)."),
) -> None:
    """Start the GoldenAnalysis MCP server (requires the [mcp] extra)."""
    try:
        from goldenanalysis.mcp import server as mcp_server
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        typer.echo(f"MCP not available: {exc} (install goldenanalysis[mcp])", err=True)
        raise typer.Exit(1) from exc

    if transport == "http":
        mcp_server.run_server_http(host=host, port=port)
    elif transport == "stdio":
        mcp_server.run_server()
    else:
        typer.echo(f"unknown --transport {transport!r} (want stdio|http)", err=True)
        raise typer.Exit(2)


if __name__ == "__main__":  # pragma: no cover
    app()
