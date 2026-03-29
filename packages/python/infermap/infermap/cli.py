"""infermap CLI — map, apply, inspect, validate commands."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(name="infermap", help="Inference-driven schema mapping engine.", add_completion=False)

logger = logging.getLogger("infermap")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool, debug: bool) -> None:
    """Configure the infermap logger based on verbosity flags."""
    inf_logger = logging.getLogger("infermap")
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING
    inf_logger.setLevel(level)
    if not inf_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        inf_logger.addHandler(handler)


def _print_table(result) -> None:
    """Print a MapResult as a formatted table to stdout."""
    typer.echo(f"{'SOURCE':<30} {'TARGET':<30} {'CONF':>6}  REASONING")
    typer.echo("-" * 90)
    for m in result.mappings:
        reasoning_short = m.reasoning[:40] + "..." if len(m.reasoning) > 40 else m.reasoning
        typer.echo(f"{m.source:<30} {m.target:<30} {m.confidence:>6.3f}  {reasoning_short}")
    if result.unmapped_source:
        typer.echo(f"\nUnmapped source fields: {', '.join(result.unmapped_source)}")
    if result.unmapped_target:
        typer.echo(f"Unmapped target fields: {', '.join(result.unmapped_target)}")
    if result.warnings:
        typer.echo("\nWarnings:")
        for w in result.warnings:
            typer.echo(f"  ! {w}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def map(
    source: str = typer.Argument(..., help="Source data (CSV path, DataFrame, DB URI, schema YAML)"),
    target: str = typer.Argument(..., help="Target data (same variety of inputs)"),
    table: Optional[str] = typer.Option(None, "--table", help="Optional table name for DB sources"),
    required: Optional[str] = typer.Option(None, "--required", help="Comma-separated required target field names"),
    schema_file: Optional[str] = typer.Option(None, "--schema-file", help="Path to schema YAML file"),
    format: str = typer.Option("table", "--format", help="Output format: table, json, or yaml"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Save mapping config to this YAML file"),
    min_confidence: float = typer.Option(0.3, "--min-confidence", help="Minimum confidence threshold"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Enable verbose logging"),
    debug: bool = typer.Option(False, "--debug/--no-debug", help="Enable debug logging"),
) -> None:
    """Map fields from SOURCE to TARGET schema."""
    _setup_logging(verbose, debug)

    from infermap.engine import MapEngine

    required_list: list[str] | None = None
    if required:
        required_list = [r.strip() for r in required.split(",") if r.strip()]

    kwargs: dict = {}
    if table:
        kwargs["table"] = table

    try:
        engine = MapEngine(min_confidence=min_confidence)
        result = engine.map(
            source,
            target,
            required=required_list,
            schema_file=schema_file,
            **kwargs,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Output
    if format == "json":
        typer.echo(result.to_json())
    elif format == "yaml":
        import yaml as _yaml
        typer.echo(_yaml.dump(result.report(), default_flow_style=False, sort_keys=False))
    else:
        _print_table(result)

    # Save config if requested
    if output:
        try:
            result.to_config(output)
            if verbose:
                typer.echo(f"Config saved to: {output}")
        except Exception as exc:
            typer.echo(f"Warning: could not save config: {exc}", err=True)


@app.command()
def apply(
    source: str = typer.Argument(..., help="Source CSV file to apply mapping to"),
    config: str = typer.Option(..., "--config", help="Path to mapping config YAML (required)"),
    output: str = typer.Option(..., "-o", "--output", help="Output CSV path (required)"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Enable verbose logging"),
) -> None:
    """Apply a saved mapping config to a source CSV, writing renamed columns to OUTPUT."""
    _setup_logging(verbose, False)

    from infermap.config import from_config
    from infermap.errors import ConfigError, ApplyError

    # Load config
    try:
        result = from_config(config)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Read source CSV
    try:
        import polars as pl
        df = pl.read_csv(source)
    except Exception as exc:
        typer.echo(f"Error reading source file: {exc}", err=True)
        raise typer.Exit(code=1)

    # Apply mapping
    try:
        df_out = result.apply(df)
    except ApplyError as exc:
        typer.echo(f"Apply error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Write output
    try:
        df_out.write_csv(output)
        if verbose:
            typer.echo(f"Written {len(df_out)} rows to: {output}")
    except Exception as exc:
        typer.echo(f"Error writing output: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def inspect(
    source: str = typer.Argument(..., help="Source data to inspect (CSV path, schema YAML, etc.)"),
    table: Optional[str] = typer.Option(None, "--table", help="Table name for DB sources"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Enable verbose logging"),
) -> None:
    """Inspect a data source: show fields, types, samples, and stats."""
    _setup_logging(verbose, False)

    from infermap.providers import extract_schema

    kwargs: dict = {}
    if table:
        kwargs["table"] = table

    try:
        schema = extract_schema(source, **kwargs)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Source: {schema.source_name or source}")
    typer.echo(f"Fields: {len(schema.fields)}")
    typer.echo("")
    typer.echo(f"{'FIELD':<30} {'TYPE':<12} {'NULL%':>6}  {'UNIQ%':>6}  SAMPLES")
    typer.echo("-" * 85)
    for f in schema.fields:
        samples = ", ".join(str(s) for s in f.sample_values[:3])
        typer.echo(
            f"{f.name:<30} {f.dtype:<12} {f.null_rate * 100:>5.1f}%  {f.unique_rate * 100:>5.1f}%  {samples}"
        )


@app.command()
def validate(
    source: str = typer.Argument(..., help="Source data to validate (CSV path, etc.)"),
    config: str = typer.Option(..., "--config", help="Path to mapping config YAML (required)"),
    required: Optional[str] = typer.Option(None, "--required", help="Comma-separated required field names"),
    strict: bool = typer.Option(False, "--strict/--no-strict", help="Exit code 1 if required fields unmapped"),
    verbose: bool = typer.Option(False, "--verbose/--no-verbose", help="Enable verbose logging"),
) -> None:
    """Validate that source columns exist for a saved mapping config."""
    _setup_logging(verbose, False)

    from infermap.config import from_config
    from infermap.errors import ConfigError
    from infermap.providers import extract_schema

    # Load config
    try:
        mapping_result = from_config(config)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Extract source schema
    try:
        schema = extract_schema(source)
    except Exception as exc:
        typer.echo(f"Error reading source: {exc}", err=True)
        raise typer.Exit(code=1)

    source_cols = {f.name for f in schema.fields}

    # Check each mapping
    missing_sources: list[str] = []
    for m in mapping_result.mappings:
        if m.source not in source_cols:
            missing_sources.append(m.source)

    if missing_sources:
        typer.echo(f"Missing source columns: {', '.join(missing_sources)}")
    else:
        typer.echo("All mapped source columns are present.")

    # Check required fields
    required_list: list[str] = []
    if required:
        required_list = [r.strip() for r in required.split(",") if r.strip()]

    mapped_targets = {m.target for m in mapping_result.mappings}
    missing_required: list[str] = [r for r in required_list if r not in mapped_targets]

    if missing_required:
        typer.echo(f"Required fields not mapped: {', '.join(missing_required)}")
        if strict:
            raise typer.Exit(code=1)
    elif required_list:
        typer.echo("All required fields are mapped.")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """infermap CLI."""
    if ctx.invoked_subcommand is None:
        typer.echo("infermap — use --help for available commands.")
