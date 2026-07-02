"""CLI dedupe command for GoldenMatch."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goldenmatch.config.loader import load_config

console = Console()
err_console = Console(stderr=True)


def _parse_file_source(raw: str) -> tuple[str, str]:
    """Parse 'file_path:source_name' handling Windows drive letters.

    Only split on the last colon if the first part is longer than 1 char
    (to avoid treating C: in C:\\path as a separator).
    """
    # Find last colon
    idx = raw.rfind(":")
    if idx <= 0:
        # No colon or colon at position 0 -> treat whole thing as path
        return (raw, Path(raw).stem)
    # Check if first part is a single char (Windows drive letter like C:)
    left = raw[:idx]
    if len(left) == 1 and left.isalpha():
        # This is a drive letter, not a separator
        return (raw, Path(raw).stem)
    return (left, raw[idx + 1:])


def _resolve_column_maps(parsed_files, cfg):
    """Match CLI files against config input.files to pick up column_map settings.

    Returns list of (path, source_name, column_map_or_None) tuples.
    """
    config_files = {}
    if cfg.input and hasattr(cfg.input, "files") and cfg.input.files:
        for fc in cfg.input.files:
            config_files[Path(fc.path).name] = fc
    elif cfg.input and hasattr(cfg.input, "file_a") and cfg.input.file_a:
        config_files[Path(cfg.input.file_a.path).name] = cfg.input.file_a
        if cfg.input.file_b:
            config_files[Path(cfg.input.file_b.path).name] = cfg.input.file_b

    result = []
    for file_path, source_name in parsed_files:
        fname = Path(file_path).name
        col_map = None
        if fname in config_files:
            fc = config_files[fname]
            col_map = fc.column_map
            if fc.source_name and source_name == Path(file_path).stem:
                source_name = fc.source_name
        result.append((file_path, source_name, col_map))
    return result


def dedupe_cmd(
    files: list[str] = typer.Argument(
        ..., help="Input files as path or path:source_name"
    ),
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to YAML config file (optional - auto-detects if omitted)"
    ),
    tui: bool = typer.Option(
        False, "--tui",
        help="Open the interactive review TUI instead of running directly (auto-config path only).",
    ),
    no_tui: bool = typer.Option(
        False, "--no-tui",
        help="Deprecated: non-interactive is now the default. Accepted for back-compat (no-op).",
    ),
    model: str | None = typer.Option(None, "--model", help="Override embedding model selection"),
    preview: bool = typer.Option(False, "--preview", help="Preview results without writing files"),
    preview_size: int = typer.Option(10000, "--preview-size", help="Number of records for preview sample"),
    preview_random: bool = typer.Option(False, "--preview-random", help="Random sample instead of first N"),
    output_golden: bool = typer.Option(False, "--output-golden", help="Output golden records"),
    output_clusters: bool = typer.Option(False, "--output-clusters", help="Output cluster info"),
    output_dupes: bool = typer.Option(False, "--output-dupes", help="Output duplicate records"),
    output_unique: bool = typer.Option(False, "--output-unique", help="Output unique records"),
    output_all: bool = typer.Option(False, "--output-all", help="Output all result types"),
    output_report: bool = typer.Option(False, "--output-report", help="Generate summary report"),
    html_report: bool = typer.Option(False, "--html-report", help="Generate standalone HTML report"),
    dashboard: bool = typer.Option(False, "--dashboard", help="Generate before/after data quality dashboard"),
    across_files_only: bool = typer.Option(False, "--across-files-only", help="Only match across different sources"),
    output_dir: str | None = typer.Option(None, "--output-dir", help="Output directory"),
    format: str | None = typer.Option(None, "--format", "-f", help="Output format (csv, parquet)"),
    run_name: str | None = typer.Option(None, "--run-name", help="Run name for output files"),
    auto_fix: bool = typer.Option(False, "--auto-fix", help="Run auto-fix before matching"),
    auto_block: bool = typer.Option(False, "--auto-block", help="Auto-suggest blocking keys"),
    chunked: bool = typer.Option(False, "--chunked", help="Large dataset mode - process in chunks for files >1M records"),
    chunk_size: int = typer.Option(100000, "--chunk-size", help="Records per chunk in chunked mode"),
    diff: bool = typer.Option(False, "--diff", help="Generate before/after CSV diff"),
    diff_html: bool = typer.Option(False, "--diff-html", help="Generate before/after HTML diff with highlighting"),
    merge_preview: bool = typer.Option(False, "--merge-preview", help="Show merge preview (what will change) without writing"),
    anomalies: bool = typer.Option(False, "--anomalies", help="Detect suspicious/fake records"),
    anomaly_sensitivity: str = typer.Option("medium", "--anomaly-sensitivity", help="low, medium, or high"),
    llm_boost: bool = typer.Option(False, "--llm-boost", help="Boost accuracy with LLM-labeled training data"),
    llm_retrain: bool = typer.Option(False, "--llm-retrain", help="Force re-labeling (ignore saved model)"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM provider: auto, anthropic, or openai"),
    llm_max_labels: int = typer.Option(500, "--llm-max-labels", help="Max pairs to label with LLM"),
    backend: str | None = typer.Option(None, "--backend", help="Processing backend: default, bucket, chunked, ray, duckdb"),
    exclude_columns: str | None = typer.Option(
        None, "--exclude-columns",
        help=(
            "Comma-separated columns to skip across the suite. "
            "GoldenMatch auto-config never picks these for "
            "matchkeys/blocking; GoldenFlow transforms skip them "
            "entirely. Layered with config.exclude_columns when "
            "both are present."
        ),
    ),
    show_controller: bool = typer.Option(
        True,
        "--show-controller/--hide-controller",
        help="Render AutoConfigController telemetry (stop_reason, decisions, Path Y) when auto-config ran. No-op for runs with an explicit --config.",
    ),
    suggest: bool = typer.Option(
        False, "--suggest",
        help="Show verified config-improvement suggestions for this run.",
    ),
    heal: bool = typer.Option(
        False, "--heal",
        help="Apply the suggestion heal loop and print the applied trail.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output"),
) -> None:
    """Run deduplication on one or more input files."""
    # Whether this run resolved its config via auto-config (zero-config path).
    # The default-run healer hint only fires here -- the free trigger reads the
    # controller history that only the auto-config path produces.
    _used_autoconfig = False
    # Telemetry captured if/when auto_configure runs in this command. Stays
    # None on the explicit-config path; the panel is suppressed in that case.
    _ctrl_profile: object = None
    _ctrl_history: object = None
    _ctrl_committed_config: object = None
    # Parse file:source pairs
    parsed_files = [_parse_file_source(f) for f in files]

    # Reject clearly-structured non-tabular inputs early with a helpful message.
    # Without this a .json/.xml file is routed to the text loader and surfaces as
    # a confusing "no data to configure on" instead of "this format isn't tabular".
    _structured_suffixes = {".json", ".jsonl", ".ndjson", ".xml", ".yaml", ".yml"}
    for _fp, _ in parsed_files:
        _fp_str = str(_fp)
        if "://" in _fp_str:
            continue  # cloud URI -- let the connector decide
        _suffix = Path(_fp_str).suffix.lower()
        if _suffix in _structured_suffixes:
            raise typer.BadParameter(
                f"{_fp_str!r} looks like a structured non-tabular format "
                f"({_suffix}). GoldenMatch dedupes tabular files "
                "(CSV/TSV/Parquet/Excel); convert it to CSV first.",
                param_hint="FILES",
            )

    # Positive-value guards on size flags -- a negative/zero value was silently
    # accepted (benign on tiny data, but a negative chunk size on a real chunked
    # run is undefined).
    if chunk_size < 1:
        raise typer.BadParameter("--chunk-size must be >= 1.", param_hint="--chunk-size")
    if preview_size < 1:
        raise typer.BadParameter(
            "--preview-size must be >= 1.", param_hint="--preview-size"
        )

    # Load config - from file, project settings, or auto-detect
    if config:
        try:
            cfg = load_config(config)
        except (FileNotFoundError, ValueError) as exc:
            if not quiet:
                console.print(f"[red]Config error:[/red] {exc}")
            raise typer.Exit(code=1)
    else:
        # Try project settings first
        from goldenmatch.config.settings import load_project_settings
        project = load_project_settings()
        if project and "matchkeys" in project:
            try:
                from goldenmatch.config.schemas import GoldenMatchConfig
                cfg = GoldenMatchConfig(**project)
                if not quiet:
                    console.print("[green]Loaded project settings from .goldenmatch.yaml[/green]")
            except Exception:
                project = None

        if not project or "matchkeys" not in (project or {}):
            # Auto-configure from input files
            try:
                from goldenmatch.cli._controller_render import capture_controller_state
                from goldenmatch.core.autoconfig import auto_configure
                if not quiet:
                    console.print("[yellow]No config file - auto-detecting column types...[/yellow]")
                cfg = auto_configure(parsed_files)
                # Pull controller telemetry off the ContextVar set by
                # auto_configure_df. None on legacy paths that bypass it.
                _ctrl_profile, _ctrl_history = capture_controller_state()
                _ctrl_committed_config = cfg
                _used_autoconfig = True
                if not quiet:
                    console.print("[green]Auto-config complete. Launching TUI for review...[/green]")
            except Exception as exc:
                if not quiet:
                    console.print(f"[red]Auto-config error:[/red] {exc}")
                raise typer.Exit(code=1)

            # Override model if specified
            if model:
                for mk in cfg.get_matchkeys():
                    for f in mk.fields:
                        if f.scorer in ("embedding", "record_embedding"):
                            f.model = model

            # Launch the interactive TUI only when explicitly requested via
            # --tui. The default is non-interactive (run auto-config, write
            # output, print a summary) so `goldenmatch dedupe file.csv` delivers
            # CSV-in/CSV-out without a full-screen app. --no-tui stays accepted
            # (now a no-op) for back-compat.
            if tui and not no_tui and not preview:
                from goldenmatch.tui.app import GoldenMatchApp
                file_paths = [fp for fp, _name in parsed_files]
                tui_app = GoldenMatchApp(files=file_paths)
                tui_app.current_config = cfg
                tui_app.run()
                raise typer.Exit(code=0)

    # ── Preview mode ──
    if preview:
        from goldenmatch.core.preview import (
            format_preview_clusters,
            format_preview_golden,
            format_preview_stats,
            format_score_histogram,
        )
        from goldenmatch.tui.engine import MatchEngine

        file_paths = [fp for fp, _name in parsed_files]
        engine = MatchEngine(file_paths)

        if preview_size < engine.row_count:
            err_console.print(
                f"[yellow]Previewing {preview_size} of {engine.row_count} records.[/yellow]",
            )

        result = engine.run_sample(cfg, sample_size=preview_size)

        err_console.print(format_preview_stats(result.stats))
        err_console.print(
            format_preview_clusters(result.clusters, engine.data, max_clusters=10),
        )
        err_console.print(format_preview_golden(result.golden, max_records=10))
        err_console.print(
            format_score_histogram([s for _, _, s in result.scored_pairs]),
        )

        run_full = typer.confirm("Run full job now?", default=False)
        if not run_full:
            raise typer.Exit(code=0)
        # Fall through to normal dedupe

    # Apply CLI overrides
    if output_dir:
        cfg.output.directory = output_dir
    if format:
        cfg.output.format = format
    if run_name:
        cfg.output.run_name = run_name

    if output_all:
        output_golden = True
        output_clusters = True
        output_dupes = True
        output_unique = True
        output_report = True

    # Zero-config default: a bare `goldenmatch dedupe file.csv` (auto-config path,
    # no explicit output flag) writes golden records by default, so "CSV in ->
    # CSV out" actually happens without needing --output-golden. Confined to the
    # auto-config path -- an explicit --config run keeps its exact prior behavior.
    if (
        _used_autoconfig
        and not preview
        and not merge_preview
        and not any([
            output_golden, output_clusters, output_dupes, output_unique,
            output_report, html_report, dashboard,
        ])
    ):
        output_golden = True
        if not quiet:
            console.print(
                "[dim]No output flag given -- writing golden records by default "
                "(use --output-all / --output-dir to control, --tui to review).[/dim]"
            )

    # Enable auto-fix from CLI flag
    if auto_fix:
        from goldenmatch.config.schemas import ValidationConfig
        if cfg.validation is None:
            cfg.validation = ValidationConfig(auto_fix=True)
        else:
            cfg.validation.auto_fix = True

    # Enable auto-block from CLI flag
    if auto_block:
        from goldenmatch.config.schemas import BlockingConfig
        if cfg.blocking is None:
            cfg.blocking = BlockingConfig(keys=[], auto_suggest=True)
        else:
            cfg.blocking.auto_suggest = True

    # Enable LLM boost from CLI flag
    if llm_boost or llm_retrain:
        cfg.llm_boost = True

    # Validate --format at parse time. It was only checked at WRITE time, so on a
    # large dataset the user waited for the entire matching run and then failed at
    # the last step with an unsupported-format error.
    if format and format.strip().lower() not in {"csv", "parquet", "xlsx"}:
        raise typer.BadParameter(
            f"Unsupported output format {format!r}. Valid: csv, parquet, xlsx.",
            param_hint="--format",
        )

    # Set backend from CLI flag (validate first -- an unknown value was silently
    # accepted and dropped by the auto-planner, so a user opting into a scaling
    # backend on a 100M-row run got no signal their choice wasn't honored).
    if backend:
        _valid_backends = {
            "default", "auto", "bucket", "chunked",
            "ray", "duckdb", "datafusion", "polars-direct",
        }
        if backend.strip().lower() not in _valid_backends:
            raise typer.BadParameter(
                f"Unknown backend {backend!r}. "
                "Valid: default, bucket, chunked, ray, duckdb.",
                param_hint="--backend",
            )
        cfg.backend = backend

    # Resolve column maps from config input.files section
    file_specs = _resolve_column_maps(parsed_files, cfg)

    # Merge --exclude-columns into the resolved config. Mutation happens
    # AFTER auto-config has built the config (if zero-config path) so
    # detector exclusions are already in place; user CLI exclusions
    # layer on top additively. See spec
    # docs/superpowers/specs/2026-05-21-exclude-columns-surfaces-design.md.
    from goldenmatch._exclusions_schema import merge_exclude_columns_into_config
    from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS
    _resolved_excludes = merge_exclude_columns_into_config(cfg, exclude_columns)
    if _resolved_excludes and not quiet:
        console.print(
            f"[dim]exclude_columns ({len(_resolved_excludes)}): "
            f"{', '.join(_resolved_excludes)}[/dim]",
        )
    _excl_token = (
        _RUNTIME_EXCLUDE_COLUMNS.set(list(_resolved_excludes))
        if _resolved_excludes else None
    )

    # Run dedupe
    try:
        from goldenmatch.core.pipeline import run_dedupe

        results = run_dedupe(
            files=file_specs,
            config=cfg,
            output_golden=output_golden,
            output_clusters=output_clusters,
            output_dupes=output_dupes,
            output_unique=output_unique,
            output_report=output_report,
            across_files_only=across_files_only,
            llm_retrain=llm_retrain,
            llm_provider=llm_provider,
            llm_max_labels=llm_max_labels,
        )
    except Exception as exc:
        if _excl_token is not None:
            _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)
        if not quiet:
            console.print(f"[red]Runtime error:[/red] {exc}")
        raise typer.Exit(code=3)
    if _excl_token is not None:
        _RUNTIME_EXCLUDE_COLUMNS.reset(_excl_token)

    # Show AutoConfigController telemetry before the report. We only render
    # when the controller actually ran in this command (i.e., auto-config
    # path) — explicit --config skips this entirely.
    if (
        show_controller
        and not quiet
        and _ctrl_history is not None
        and _ctrl_committed_config is not None
    ):
        from goldenmatch.cli._controller_render import render_controller_panel
        err_console.print(render_controller_panel(
            profile=_ctrl_profile,
            history=_ctrl_history,
            committed_config=_ctrl_committed_config,
            verbose=verbose,
        ))

    # Print report
    if not quiet and results.get("report"):
        report = results["report"]
        table = Table(title="Dedupe Report")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        for key, val in report.items():
            if key == "cluster_size_distribution":
                val = dict(val)
            table.add_row(str(key), str(val))
        console.print(table)
    elif not quiet:
        clusters = results.get("clusters", {})
        console.print(f"Dedupe complete. {len(clusters)} clusters found.")

    # ── Config-suggestion (healer) surface ──
    # ADVISORY and additive: a default run prints a one-line hint when the free
    # trigger surfaced candidates; --suggest prints them; --heal applies the loop
    # and prints the trail. Wrapped so a healer failure NEVER breaks a dedupe.
    _emit_healer_surface(
        file_specs, cfg, results,
        suggest=suggest, heal=heal, quiet=quiet,
        used_autoconfig=_used_autoconfig,
    )


def _load_combined_frame(file_specs):
    """Load + concat the input files into one frame for the advisory healer.

    Mirrors the ingest the dedupe pipeline does (load + optional column_map),
    but skips source/row-id bookkeeping -- ``dedupe_df`` handles that itself.
    """
    import polars as pl

    from goldenmatch.core.ingest import apply_column_map, load_file

    frames = []
    for spec in file_specs:
        path = spec[0]
        col_map = spec[2] if len(spec) == 3 else None
        lf = load_file(path)
        if col_map:
            lf = apply_column_map(lf, col_map)
        frames.append(lf.collect())
    if len(frames) == 1:
        return frames[0]
    return pl.concat(frames, how="diagonal_relaxed")


def _render_suggestion_lines(items) -> list[str]:
    """One ASCII line per serialized suggestion dict: kind + target + rationale."""
    lines = []
    for s in items:
        kind = s.get("kind", "")
        target = s.get("target", "")
        rationale = s.get("rationale", "")
        lines.append(f"- {kind}: {target} - {rationale}")
    return lines


def _emit_healer_surface(
    file_specs, cfg, results, *, suggest: bool, heal: bool, quiet: bool, used_autoconfig: bool
) -> None:
    """Advisory config-suggestion (healer) surface. NEVER raises.

    Default run (no flags): one-line hint to stderr when the FREE headroom trigger
    fires on the run that already happened (reads `results["postflight_report"]` --
    NO second pipeline). Zero-config path only; kill-switch
    ``GOLDENMATCH_SUGGEST_ON_DEDUPE=0``. ``--suggest``/``--heal`` (opt-in) DO pay a
    `dedupe_df` re-run, because the file-based `run_dedupe` result carries no
    `scored_pairs` for the kernel; that cost is accepted by explicit request.
    """
    import os

    if not (suggest or heal):
        # Default hint: FREE trigger only. Respects --quiet + kill-switch + the
        # zero-config gate (an explicit --config never ran the controller).
        if quiet or used_autoconfig is False:
            return
        if os.environ.get("GOLDENMATCH_SUGGEST_ON_DEDUPE", "1").strip() == "0":
            return
        try:
            from goldenmatch.core.suggest.surface import headroom_signal

            class _R:  # shim so headroom_signal can read .postflight_report
                postflight_report = (results or {}).get("postflight_report")

            if headroom_signal(_R()) is not None:
                err_console.print(
                    "[yellow]Config-improvement suggestions may be available - "
                    "re-run with --suggest to see them, --heal to apply.[/yellow]"
                )
        except Exception:  # noqa: BLE001 - advisory; never break dedupe
            pass
        return

    try:
        from goldenmatch import _api

        df = _load_combined_frame(file_specs)

        if heal:
            result = _api.dedupe_df(df, config=cfg, heal=True)
            trail = result.heal_trail or []
            if trail:
                console.print(
                    f"[green]Config healed - {len(trail)} suggestion(s) applied:[/green]"
                )
                for line in _render_suggestion_lines(trail):
                    console.print(line)
                console.print(
                    "[dim]Output above reflects the pre-heal config; re-run with "
                    "the healed config to apply it to the written output.[/dim]"
                )
            else:
                console.print("Healer found nothing to apply - config looks good.")
        elif suggest:
            result = _api.dedupe_df(df, config=cfg, suggest=True)
            items = result.suggestions or []
            if items:
                console.print(f"[cyan]{len(items)} suggestion(s):[/cyan]")
                for line in _render_suggestion_lines(items):
                    console.print(line)
            else:
                console.print("No suggestions - config looks good.")
    except Exception as exc:  # noqa: BLE001 - healer is advisory; never break dedupe
        if not quiet:
            err_console.print(f"[dim]suggestions unavailable: {exc}[/dim]")
