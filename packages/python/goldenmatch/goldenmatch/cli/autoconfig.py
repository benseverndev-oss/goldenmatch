"""`goldenmatch autoconfig` — run AutoConfigController and report what it picked.

Equivalent of the web UI's "Auto-configure" button and the TUI's Ctrl+A
binding: profiles the input data, runs the iterative refit loop, and
prints the committed config + telemetry. Does NOT run the pipeline.

Two modes:

  * Default — prints the committed config as YAML on stdout and the
    controller telemetry panel on stderr. Pipe stdout to a file to save:
    ``goldenmatch autoconfig data.csv > goldenmatch.yml``
  * ``--out PATH`` — writes the YAML to ``PATH`` instead of stdout.
    Useful when you want both the panel and the file in one run.

The telemetry panel is the same one ``dedupe`` shows after a zero-config
run, so debugging "why did auto-config decide X?" doesn't require running
the full pipeline.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

err_console = Console(stderr=True)
out_console = Console()


def autoconfig_cmd(
    files: list[str] = typer.Argument(
        ..., help="Input files as path or path:source_name"
    ),
    out: str | None = typer.Option(
        None, "--out", "-o", help="Write committed config to this path instead of stdout."
    ),
    domain: str | None = typer.Option(
        None, "--domain", help="Pin a domain rulebook (electronics, software, …) instead of auto-detecting.",
    ),
    show_controller: bool = typer.Option(
        True,
        "--show-controller/--hide-controller",
        help="Render the controller telemetry panel on stderr.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include indicator priors + decision trace in the panel."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress all stderr output (panel + status messages)."),
) -> None:
    """Run auto-configuration on input files and print/write the committed config."""
    from goldenmatch.cli._controller_render import (
        capture_controller_state,
        render_controller_panel,
        render_short_status,
    )
    from goldenmatch.cli.dedupe import _parse_file_source

    parsed = [_parse_file_source(f) for f in files]

    if not quiet:
        err_console.print(f"[yellow]Auto-configuring from {len(parsed)} file(s)…[/yellow]")

    try:
        cfg = _run_autoconfig(parsed, domain=domain)
    except Exception as exc:
        err_console.print(f"[red]autoconfig failed:[/red] {exc}")
        raise typer.Exit(code=1)

    profile, history = capture_controller_state()

    # Render telemetry on stderr so stdout stays clean for piping.
    if show_controller and not quiet:
        err_console.print(render_controller_panel(
            profile=profile,
            history=history,
            committed_config=cfg,
            verbose=verbose,
        ))
    elif not quiet:
        # Even with the panel hidden, emit a one-line status so the user
        # can see in CI logs whether the controller succeeded.
        err_console.print(render_short_status(
            profile=profile,
            history=history,
            committed_config=cfg,
        ))

    # Serialize the committed config to YAML and emit on stdout or to --out.
    yaml_blob = _config_to_yaml(cfg)
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml_blob, encoding="utf-8")
        if not quiet:
            err_console.print(f"[green]Wrote config to[/green] {out_path}")
    else:
        # No Rich markup here — stdout is meant to be piped to a YAML file.
        # The print() bypasses Rich's renderer.
        print(yaml_blob, end="")


def _run_autoconfig(
    parsed_files: list[tuple[str, str]],
    *,
    domain: str | None,
):
    """Invoke ``auto_configure`` with optional domain pin.

    ``auto_configure`` (file-based) doesn't accept a domain_config today.
    When a domain is requested, we round-trip through ``auto_configure_df``
    to attach a ``DomainConfig`` before the controller starts.
    """
    from goldenmatch.core.autoconfig import auto_configure
    if domain is None:
        return auto_configure(parsed_files)

    import polars as pl

    from goldenmatch.config.schemas import DomainConfig
    from goldenmatch.core.autoconfig import auto_configure_df

    dfs = []
    for path, _src in parsed_files:
        p = Path(path)
        if p.suffix.lower() in (".xlsx", ".xls"):
            df = pl.read_excel(p, engine="openpyxl")
        elif p.suffix.lower() == ".parquet":
            df = pl.read_parquet(p)
        else:
            df = pl.read_csv(p, encoding="utf8-lossy", infer_schema_length=10000, ignore_errors=True)
        dfs.append(df)
    combined = pl.concat(dfs, how="diagonal") if len(dfs) > 1 else dfs[0]
    return auto_configure_df(combined, domain_config=DomainConfig(enabled=True, mode=domain))


def _config_to_yaml(cfg) -> str:
    """Serialize ``GoldenMatchConfig`` to YAML using Pydantic + PyYAML.

    Pydantic's ``model_dump(mode="json")`` gives us the wire shape; PyYAML
    dumps it with stable key order. ``exclude_none=True`` keeps the output
    compact — unset fields don't appear.
    """
    import yaml

    blob = cfg.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(blob, sort_keys=False, default_flow_style=False)
