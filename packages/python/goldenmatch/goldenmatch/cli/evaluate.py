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
    ground_truth: Path = typer.Option(..., "--ground-truth", "--gt", help="Ground truth CSV path"),
    col_a: str = typer.Option("id_a", "--col-a", help="Ground truth column A"),
    col_b: str = typer.Option("id_b", "--col-b", help="Ground truth column B"),
    threshold: float | None = typer.Option(None, "--threshold", "-t", help="Override match threshold"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save results to JSON"),
    min_f1: float | None = typer.Option(None, "--min-f1", help="Minimum F1 score (exit code 1 if below). For CI/CD quality gates."),
    min_precision: float | None = typer.Option(None, "--min-precision", help="Minimum precision (exit code 1 if below)"),
    min_recall: float | None = typer.Option(None, "--min-recall", help="Minimum recall (exit code 1 if below)"),
    threshold_sweep_flag: bool = typer.Option(False, "--threshold-sweep", help="Sweep the link threshold over scored pairs: P/R/F1 table + recommended cut (+ Fellegi-Sunter m/u model report when a probabilistic matchkey ran)."),
) -> None:
    """Evaluate matching quality against ground truth pairs.

    Use --min-f1, --min-precision, --min-recall as CI/CD quality gates:
    goldenmatch evaluate data.csv -c config.yaml --gt gt.csv --min-f1 0.90

    Add --threshold-sweep for an operating-point curve over the scored pairs
    (which cut to use) plus, for Fellegi-Sunter matchkeys, the m/u match-weight
    model report.
    """
    from goldenmatch.core.pipeline import run_dedupe

    if not ground_truth.exists():
        err_console.print(f"[red]Ground truth file not found: {ground_truth}[/red]")
        raise typer.Exit(1)

    cfg = load_config(str(config))

    # Override threshold if specified
    if threshold is not None:
        for mk in cfg.get_matchkeys():
            if mk.threshold is not None:
                mk.threshold = threshold

    parsed = [_parse_file_source(f) for f in files]
    file_specs = _resolve_column_maps(parsed, cfg)

    gt_pairs = load_ground_truth_csv(str(ground_truth), col_a, col_b)

    console.print(f"[bold]Evaluating with {len(gt_pairs)} ground truth pairs...[/bold]\n")

    # The threshold sweep needs scored pairs (+ trained FS models), which
    # run_dedupe doesn't surface — route through MatchEngine, which exposes
    # EngineResult.scored_pairs + .em_results. Without the flag, keep the
    # existing run_dedupe path unchanged.
    sweep_payload: dict | None = None
    if threshold_sweep_flag:
        from goldenmatch.tui.engine import MatchEngine
        engine = MatchEngine([p[0] for p in parsed])
        eng = engine.run_full(cfg)
        clusters = eng.clusters
        sweep_payload = _emit_threshold_sweep(
            eng.scored_pairs, gt_pairs, getattr(eng, "em_results", None) or {}, cfg,
        )
    else:
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
        payload = dict(summary)
        if sweep_payload is not None:
            payload["threshold_sweep"] = sweep_payload
        output.write_text(json.dumps(payload, indent=2))
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


def _emit_threshold_sweep(scored_pairs, gt_pairs, em_results, cfg) -> dict:
    """Print the link-threshold sweep + recommended cut (+ FS m/u report).

    Returns a JSON-serializable payload (sweep rows, recommended cut, and any
    Fellegi-Sunter model reports) for ``--output``.
    """
    from goldenmatch.core.evaluate import (
        fs_model_report,
        probability_two_random_records_match,
        recommend_threshold,
    )

    rec = recommend_threshold(scored_pairs, gt_pairs)
    sweep = rec.get("sweep", [])

    table = Table(title="Threshold sweep (operating points)", show_header=True)
    for col, just in (("Threshold", "right"), ("Precision", "right"),
                      ("Recall", "right"), ("F1", "right"), ("Pred pairs", "right")):
        table.add_column(col, justify=just, style="cyan" if col == "Threshold" else None)
    best_t = rec.get("threshold")
    for row in sweep:
        mark = "  <- best F1" if row["threshold"] == best_t else ""
        table.add_row(
            f"{row['threshold']:.4f}", f"{row['precision']:.1%}", f"{row['recall']:.1%}",
            f"{row['f1']:.1%}{mark}", str(row["predicted_pairs"]),
        )
    console.print(table)
    if sweep:
        console.print(
            f"[bold green]Recommended cut: {best_t:.4f}[/bold green] "
            f"(P={rec['precision']:.1%}, R={rec['recall']:.1%}, F1={rec['f1']:.1%})\n"
        )
    else:
        console.print("[yellow]No scored pairs to sweep.[/yellow]\n")

    # Fellegi-Sunter model report (m/u match weights) per probabilistic matchkey.
    fs_reports: dict[str, dict] = {}
    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) != "probabilistic" or mk.name not in em_results:
            continue
        em = em_results[mk.name]
        report = fs_model_report(em, mk)
        fs_reports[mk.name] = report
        lam = probability_two_random_records_match(em)
        mt = Table(
            title=f"Fellegi-Sunter model · {mk.name}  "
                  f"(P(2 random match)={lam:.5f}, prior={report['prior_bits']:+.2f} bits, "
                  f"converged={report['converged']}, iters={report['iterations']})",
            show_header=True,
        )
        for col in ("Field", "Level", "m=P(l|match)", "u=P(l|non)", "weight (bits)"):
            mt.add_column(col, justify="right" if col != "Field" else "left")
        for fld in report["fields"]:
            for lvl in fld["levels"]:
                mt.add_row(
                    fld["field"] if lvl["level"] == 0 else "",
                    str(lvl["level"]),
                    "n/a" if lvl["m"] is None else f"{lvl['m']:.4f}",
                    "n/a" if lvl["u"] is None else f"{lvl['u']:.4f}",
                    "n/a" if lvl["weight_bits"] is None else f"{lvl['weight_bits']:+.2f}",
                )
        console.print(mt)

    payload = {"recommended": {k: v for k, v in rec.items() if k != "sweep"}, "sweep": sweep}
    if fs_reports:
        payload["fs_model"] = fs_reports
    return payload
