from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="goldenflow",
    help="GoldenFlow: data transformation toolkit.",
    invoke_without_command=True,
)


def _version_callback(value: bool):
    if value:
        from goldenflow import __version__
        typer.echo(f"goldenflow {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback),
):
    pass


@app.command()
def transform(
    path: Path = typer.Argument(..., help="Input data file"),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="YAML config file"),
    output_dir: Optional[Path] = typer.Option(None, "-o", "--output-dir", help="Output directory"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Domain pack to use"),
    from_findings: bool = typer.Option(False, "--from-findings", help="Read findings from stdin"),
):
    """Transform a data file (zero-config or config-driven)."""
    import sys
    import json
    import io

    import polars as pl

    import goldenflow
    from goldenflow.config.loader import load_config
    from goldenflow.config.schema import TransformSpec
    from goldenflow.engine.selector import select_from_findings
    from goldenflow.engine.transformer import TransformEngine
    from goldenflow.reporters.rich_console import print_manifest

    cfg = load_config(config) if config else goldenflow.GoldenFlowConfig()

    if domain:
        from goldenflow.domains import load_domain
        domain_pack = load_domain(domain)
        if domain_pack:
            cfg = domain_pack.default_config

    # Handle --from-findings: read JSON findings from stdin and build config
    if from_findings:
        stdin_data = sys.stdin.read()
        findings = json.loads(stdin_data) if stdin_data.strip() else []
        if isinstance(findings, dict):
            findings = findings.get("findings", [findings])
        col_transforms = select_from_findings(findings)
        cfg.transforms = [
            TransformSpec(column=col, ops=ops)
            for col, ops in col_transforms.items()
        ]

    # Handle stdin input (path == "-")
    is_stdin = str(path) == "-"
    if is_stdin:
        stdin_bytes = sys.stdin.buffer.read()
        df = pl.read_csv(io.BytesIO(stdin_bytes))
        engine = TransformEngine(config=cfg)
        result = engine.transform_df(df)
        if output_dir is None:
            # Write to stdout
            result.df.write_csv(sys.stdout)
            return
        else:
            from goldenflow.connectors.file import write_file
            output_dir.mkdir(parents=True, exist_ok=True)
            write_file(result.df, output_dir / "transformed.csv")
            result.manifest.save(output_dir / "manifest.json")
            print_manifest(result.manifest)
            return

    engine = TransformEngine(config=cfg)

    if output_dir is None:
        output_dir = path.parent

    result = engine.transform_file(path, output_dir=output_dir)
    print_manifest(result.manifest)
    typer.echo(f"\nOutput: {output_dir / (path.stem + '_transformed' + path.suffix)}")


@app.command()
def validate(
    path: Path = typer.Argument(..., help="Input data file"),
    config: Optional[Path] = typer.Option(None, "-c", "--config"),
):
    """Dry-run: show what would change without writing."""
    import goldenflow
    from goldenflow.config.loader import load_config
    from goldenflow.engine.transformer import TransformEngine
    from goldenflow.reporters.rich_console import print_manifest

    cfg = load_config(config) if config else goldenflow.GoldenFlowConfig()
    engine = TransformEngine(config=cfg)
    result = engine.transform_file(path)
    typer.echo("Dry run — would change:")
    print_manifest(result.manifest)


@app.command()
def profile(
    path: Path = typer.Argument(..., help="Input data file"),
):
    """Show column profiles for a data file."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.profiler_bridge import profile_dataframe
    from goldenflow.reporters.rich_console import print_profile

    df = read_file(path)
    prof = profile_dataframe(df, file_path=str(path))
    print_profile(prof)


@app.command()
def learn(
    path: Path = typer.Argument(..., help="Input data file"),
    output: Path = typer.Option("goldenflow.yaml", "-o", "--output", help="Output config path"),
):
    """Generate a YAML config from data patterns."""
    from goldenflow.config.learner import learn_config
    from goldenflow.config.loader import save_config

    cfg = learn_config(path)
    save_config(cfg, output)
    typer.echo(f"Config saved to {output}")


@app.command()
def diff(
    before: Path = typer.Argument(..., help="Before file"),
    after: Path = typer.Argument(..., help="After file"),
):
    """Compare pre/post transform files."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.differ import diff_dataframes
    from goldenflow.reporters.rich_console import print_diff

    df_before = read_file(before)
    df_after = read_file(after)
    result = diff_dataframes(df_before, df_after)
    print_diff(result)


@app.command(name="map")
def map_cmd(
    source: Path = typer.Option(..., "--source", "-s", help="Source data file"),
    target: Path = typer.Option(..., "--target", "-t", help="Target data file or schema"),
    config: Optional[Path] = typer.Option(None, "-c", "--config", help="Mapping config"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Save mapping config"),
):
    """Auto-map schemas between source and target."""
    from goldenflow.connectors.file import read_file
    from goldenflow.mapping.schema_mapper import SchemaMapper
    from goldenflow.reporters.rich_console import console

    source_df = read_file(source)
    target_df = read_file(target)
    mapper = SchemaMapper()
    mappings = mapper.map(source_df, target_df)

    for m in mappings:
        tier = "auto" if m.confidence >= 0.9 else "suggest" if m.confidence >= 0.6 else "skip"
        console.print(f"  {m.source} → {m.target} ({m.confidence:.2f}) [{tier}]")

    if output:
        from goldenflow.config.loader import save_config
        cfg = mapper.to_config(mappings)
        save_config(cfg, output)
        console.print(f"\nMapping saved to {output}")


@app.command()
def interactive(
    path: Optional[Path] = typer.Argument(None, help="Input data file"),
):
    """Launch the interactive TUI."""
    from goldenflow.tui.app import GoldenFlowApp
    app_tui = GoldenFlowApp(path=path)
    app_tui.run()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
):
    """Launch the REST API server."""
    import uvicorn
    from goldenflow.api.server import create_app
    uvicorn.run(create_app(), host=host, port=port)


@app.command(name="mcp-serve")
def mcp_serve():
    """Start MCP server for Claude Desktop."""
    from goldenflow.mcp.server import run_server
    run_server()
