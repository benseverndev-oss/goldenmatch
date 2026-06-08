"""CLI evaluate command for GoldenMatch."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from goldenmatch.cli.dedupe import _parse_file_source, _resolve_column_maps
from goldenmatch.config.loader import load_config
from goldenmatch.core.evaluate import evaluate_clusters, load_ground_truth_csv

console = Console()
err_console = Console(stderr=True)


def evaluate_cmd(
    files: list[str] = typer.Argument(..., help="Input files (path or path:source_name)"),
    config: Path = typer.Option(..., "--config", "-c", help="Config YAML path"),
    ground_truth: Path | None = typer.Option(None, "--ground-truth", "--gt", help="Ground truth CSV path (required unless --certify)"),
    col_a: str = typer.Option("id_a", "--col-a", help="Ground truth column A"),
    col_b: str = typer.Option("id_b", "--col-b", help="Ground truth column B"),
    threshold: float | None = typer.Option(None, "--threshold", "-t", help="Override match threshold"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save results to JSON"),
    min_f1: float | None = typer.Option(None, "--min-f1", help="Minimum F1 score (exit code 1 if below). For CI/CD quality gates."),
    min_precision: float | None = typer.Option(None, "--min-precision", help="Minimum precision (exit code 1 if below)"),
    min_recall: float | None = typer.Option(None, "--min-recall", help="Minimum recall (exit code 1 if below)"),
    certify: bool = typer.Option(False, "--certify", help="Estimate recall WITHOUT ground truth (unsupervised, via capture-recapture over the config's matchkeys/passes; needs >=3)."),
) -> None:
    """Evaluate matching quality against ground truth pairs.

    Use --min-f1, --min-precision, --min-recall as CI/CD quality gates:
    goldenmatch evaluate data.csv -c config.yaml --gt gt.csv --min-f1 0.90

    With --certify, estimate recall WITHOUT ground truth (each matchkey/pass is
    treated as a decorrelated system):
    goldenmatch evaluate data.csv -c config.yaml --certify
    """
    from goldenmatch.core.pipeline import run_dedupe

    cfg = load_config(str(config))

    # Override threshold if specified
    if threshold is not None:
        for mk in cfg.get_matchkeys():
            if mk.threshold is not None:
                mk.threshold = threshold

    parsed = [_parse_file_source(f) for f in files]
    file_specs = _resolve_column_maps(parsed, cfg)

    if certify:
        _run_certify(file_specs, cfg, run_dedupe, output)
        return

    if ground_truth is None or not ground_truth.exists():
        err_console.print("[red]Ground truth required (use --gt PATH) unless --certify is set.[/red]")
        raise typer.Exit(1)

    gt_pairs = load_ground_truth_csv(str(ground_truth), col_a, col_b)

    console.print(f"[bold]Evaluating with {len(gt_pairs)} ground truth pairs...[/bold]\n")

    result = run_dedupe(file_specs, cfg)
    clusters = result["clusters"]

    eval_result = evaluate_clusters(clusters, gt_pairs)

    # Display results
    table = Table(title="Evaluation Results", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", justify="right")

    summary = eval_result.summary()
    table.add_row("Precision", f"{summary['precision']:.1%}")
    table.add_row("Recall", f"{summary['recall']:.1%}")
    table.add_row("F1 Score", f"{summary['f1']:.1%}")
    table.add_row("True Positives", str(summary["tp"]))
    table.add_row("False Positives", str(summary["fp"]))
    table.add_row("False Negatives", str(summary["fn"]))
    table.add_row("Predicted Pairs", str(summary["predicted_pairs"]))
    table.add_row("Ground Truth Pairs", str(summary["ground_truth_pairs"]))

    console.print(table)

    if output:
        import json
        output.write_text(json.dumps(summary, indent=2))
        console.print(f"\n[green]Results saved to {output}[/green]")

    # CI/CD quality gates
    failed = False
    if min_f1 is not None and summary["f1"] < min_f1:
        err_console.print(f"[red]FAIL: F1 {summary['f1']:.1%} < minimum {min_f1:.1%}[/red]")
        failed = True
    if min_precision is not None and summary["precision"] < min_precision:
        err_console.print(f"[red]FAIL: Precision {summary['precision']:.1%} < minimum {min_precision:.1%}[/red]")
        failed = True
    if min_recall is not None and summary["recall"] < min_recall:
        err_console.print(f"[red]FAIL: Recall {summary['recall']:.1%} < minimum {min_recall:.1%}[/red]")
        failed = True
    if failed:
        raise typer.Exit(1)


def _run_certify(file_specs, cfg, run_dedupe, output) -> None:
    """Estimate recall WITHOUT ground truth: run each matchkey/pass as a
    decorrelated system and apply the FP-aware capture-recapture estimator."""
    from goldenmatch.core.recall_certificate import clusters_to_pairs, estimate_recall

    matchkeys = cfg.get_matchkeys()
    if not matchkeys:
        err_console.print("[red]No matchkeys in config — cannot certify recall.[/red]")
        raise typer.Exit(1)

    # Derive K decorrelated systems. Prefer the config's real matchkeys/passes
    # (multi_pass / multi-matchkey provenance). If <3, split a multi-field
    # matchkey into per-field systems (each field = a decorrelated pass — the
    # Phase-0-validated form). Capture-recapture needs >=3 to estimate.
    systems: list[list] = [[mk] for mk in matchkeys]
    if len(systems) < 3:
        for mk in matchkeys:
            flds = getattr(mk, "fields", None) or []
            if len(flds) >= 3:
                systems = [
                    [mk.model_copy(update={"fields": [f], "name": f"{mk.name}__f{i}"})]
                    for i, f in enumerate(flds)
                ]
                break

    console.print(
        f"[bold]Estimating recall (unsupervised) from {len(systems)} decorrelated "
        f"system(s)...[/bold]\n"
    )
    pairsets: list[set] = []
    for sys_mks in systems:
        sub_cfg = cfg.model_copy(update={"matchkeys": sys_mks})
        res = run_dedupe(file_specs, sub_cfg)
        pairsets.append(clusters_to_pairs(res["clusters"]))

    est = estimate_recall(pairsets)

    table = Table(title="Recall Certificate (unsupervised)", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", justify="right")
    table.add_row("Systems (matchkeys)", str(est.n_systems))
    table.add_row("Found pairs (union)", str(est.found_pairs))
    table.add_row("System overlap", f"{est.mean_overlap:.2f}")
    if est.recall is not None:
        table.add_row("Estimated recall", f"{est.recall:.1%}")
        table.add_row("Per-system capture p", f"{est.per_system_capture_prob:.2f}")
    else:
        table.add_row("Estimated recall", "n/a")
    console.print(table)
    console.print(f"\n[dim]{est.note}[/dim]")

    if output:
        import json
        output.write_text(json.dumps({
            "estimated_recall": est.recall, "n_systems": est.n_systems,
            "found_pairs": est.found_pairs, "mean_overlap": est.mean_overlap,
            "per_system_capture_prob": est.per_system_capture_prob,
            "capture_histogram": est.capture_histogram, "estimable": est.estimable,
            "note": est.note,
        }, indent=2))
        console.print(f"\n[green]Results saved to {output}[/green]")
