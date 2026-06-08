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
    threshold_sweep_flag: bool = typer.Option(False, "--threshold-sweep", help="Sweep the link threshold over scored pairs: P/R/F1 table + recommended cut (+ Fellegi-Sunter m/u model report when a probabilistic matchkey ran)."),
    certify: bool = typer.Option(False, "--certify", help="Estimate recall WITHOUT ground truth (unsupervised, via capture-recapture over the config's matchkeys/passes; needs >=3)."),
    audit_out: Path | None = typer.Option(None, "--audit-out", help="With --certify: emit a stratified audit sample CSV to label for a SAFE recall lower bound."),
    audit_in: Path | None = typer.Option(None, "--audit-in", help="With --certify: read a labelled audit sample and print the audit-calibrated SAFE lower bound."),
    audit_n: int = typer.Option(50, "--audit-n", help="Audit samples per stratum (default 50)."),
) -> None:
    """Evaluate matching quality against ground truth pairs.

    Use --min-f1, --min-precision, --min-recall as CI/CD quality gates:
    goldenmatch evaluate data.csv -c config.yaml --gt gt.csv --min-f1 0.90

    Add --threshold-sweep for an operating-point curve over the scored pairs
    (which cut to use) plus, for Fellegi-Sunter matchkeys, the m/u match-weight
    model report.
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

    if certify or audit_in is not None:
        _run_certify(file_specs, cfg, run_dedupe, output,
                     audit_out=audit_out, audit_in=audit_in, audit_n=audit_n)
        return

    if ground_truth is None or not ground_truth.exists():
        err_console.print("[red]Ground truth required (use --gt PATH) unless --certify is set.[/red]")
        raise typer.Exit(1)

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
def _build_systems(matchkeys) -> list[list]:
    """K decorrelated systems from the config's matchkeys/passes; split a
    multi-field matchkey into per-field systems when there are <3 matchkeys."""
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
    return systems


def _system_pairsets(file_specs, cfg, systems, run_dedupe, relax: float = 1.0):
    """Run each system; return its matched row-pair set. `relax` < 1 lowers each
    matchkey threshold to surface sub-threshold candidates."""
    from goldenmatch.core.recall_certificate import clusters_to_pairs
    out = []
    for sys_mks in systems:
        if relax != 1.0:
            sys_mks = [mk.model_copy(update={"threshold": (mk.threshold or 0.85) * relax})
                       for mk in sys_mks]
        sub_cfg = cfg.model_copy(update={"matchkeys": sys_mks})
        res = run_dedupe(file_specs, sub_cfg)
        out.append(clusters_to_pairs(res["clusters"]))
    return out


def _run_certify(file_specs, cfg, run_dedupe, output,
                 audit_out=None, audit_in=None, audit_n: int = 50) -> None:
    """Estimate recall WITHOUT ground truth (capture-recapture over decorrelated
    systems). With --audit-out/--audit-in, additionally produce an audit-calibrated
    SAFE lower bound from a small labelled sample of the sub-threshold stratum."""
    from goldenmatch.core.recall_certificate import estimate_recall

    if audit_in is not None:
        _certify_ingest(audit_in)
        return

    matchkeys = cfg.get_matchkeys()
    if not matchkeys:
        err_console.print("[red]No matchkeys in config — cannot certify recall.[/red]")
        raise typer.Exit(1)

    systems = _build_systems(matchkeys)
    console.print(
        f"[bold]Estimating recall (unsupervised) from {len(systems)} decorrelated "
        f"system(s)...[/bold]\n"
    )
    pairsets = _system_pairsets(file_specs, cfg, systems, run_dedupe)
    est = estimate_recall(pairsets)

    if audit_out is not None:
        _emit_audit_sample(file_specs, cfg, run_dedupe, audit_out, audit_n)

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

    if audit_out is not None:
        console.print(
            f"\n[bold]Audit sample written to {audit_out}[/bold] — label the "
            f"`is_match` column (1/0), then run:\n  goldenmatch evaluate ... "
            f"--certify --audit-in {audit_out}"
        )

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


def _row_count(file_specs) -> int:
    import polars as pl
    total = 0
    for spec in file_specs:
        path = spec[0]
        try:
            total += pl.scan_csv(path, encoding="utf8-lossy", ignore_errors=True
                                 ).select(pl.len()).collect().item()
        except Exception:
            pass
    return total


def _emit_audit_sample(file_specs, cfg, run_dedupe, out_path, n) -> None:
    """Emit a stratified audit sample for the steward to label. Stratum A = the
    FULL-config matched pairs (the high-precision set being certified); B =
    sub-threshold candidates (relaxed-threshold matches minus strict); C =
    no-feature pairs (blocking-completeness check)."""
    import csv
    import json
    import random

    from goldenmatch.core.recall_certificate import clusters_to_pairs

    rng = random.Random(0)
    union = clusters_to_pairs(run_dedupe(file_specs, cfg)["clusters"])
    relaxed_mks = [mk.model_copy(update={"threshold": (mk.threshold or 0.85) * 0.6})
                   for mk in cfg.get_matchkeys()]
    relaxed_cfg = cfg.model_copy(update={"matchkeys": relaxed_mks})
    relaxed_union = clusters_to_pairs(run_dedupe(file_specs, relaxed_cfg)["clusters"])
    sub = relaxed_union - union

    n_rows = _row_count(file_specs)

    def _sample(pool, k):
        pool = list(pool)
        rng.shuffle(pool)
        return pool[:k]

    rows = [("A", a, b) for (a, b) in _sample(union, n)]
    rows += [("B", a, b) for (a, b) in _sample(sub, n)]
    # stratum C: random row pairs that no system (even relaxed) proposed
    seen = union | relaxed_union
    c, tries = [], 0
    while len(c) < n and tries < n * 100 and n_rows > 1:
        i, j = rng.randrange(n_rows), rng.randrange(n_rows)
        tries += 1
        if i == j:
            continue
        pr = (i, j) if i < j else (j, i)
        if pr in seen:
            continue
        c.append(pr)
    rows += [("C", a, b) for (a, b) in c]

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stratum", "row_a", "row_b", "is_match"])
        for stratum, a, b in rows:
            w.writerow([stratum, a, b, ""])
    meta = {"a_size": len(union), "b_size": len(sub)}
    with open(str(out_path) + ".meta.json", "w") as f:
        json.dump(meta, f)


def _certify_ingest(in_path) -> None:
    """Read a labelled audit sample + sizes meta, compute the safe lower bound."""
    import csv
    import json

    from goldenmatch.core.recall_certificate import audit_calibrated_bound

    meta_path = str(in_path) + ".meta.json"
    try:
        meta = json.load(open(meta_path))
    except FileNotFoundError:
        err_console.print(f"[red]Missing sizes file {meta_path} (emit with --audit-out first).[/red]")
        raise typer.Exit(1)

    tally = {"A": [0, 0], "B": [0, 0], "C": [0, 0]}  # [true, n]
    with open(in_path, newline="") as f:
        for row in csv.DictReader(f):
            s = (row.get("stratum") or "").strip().upper()
            lab = (row.get("is_match") or "").strip()
            if s not in tally or lab == "":
                continue
            tally[s][1] += 1
            if lab in ("1", "true", "yes", "y", "True"):
                tally[s][0] += 1

    cert = audit_calibrated_bound(
        found_size=meta["a_size"], sub_size=meta["b_size"],
        a_true=tally["A"][0], a_n=tally["A"][1],
        b_true=tally["B"][0], b_n=tally["B"][1],
        c_true=tally["C"][0], c_n=tally["C"][1] or None,
    )
    table = Table(title="Recall Certificate (audit-calibrated)", show_header=True)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", justify="right")
    table.add_row("Matched pairs |A|", str(cert.found_pairs))
    table.add_row("Sub-threshold |B|", str(cert.candidate_pairs))
    table.add_row("Audit labels used", str(cert.audit_labels))
    if cert.recall is not None:
        table.add_row("Estimated recall", f"{cert.recall:.1%}")
        table.add_row("SAFE lower bound", f"{cert.recall_lower:.1%}")
        table.add_row("Upper bound", f"{cert.recall_upper:.1%}")
    if cert.blocking_complete is not None:
        table.add_row("Blocking complete", "yes" if cert.blocking_complete else "NO (unsafe)")
    console.print(table)
    console.print(f"\n[dim]{cert.note}[/dim]")
