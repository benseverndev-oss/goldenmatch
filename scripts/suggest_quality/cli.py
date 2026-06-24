"""Config-suggestion quality harness CLI.

    python -m scripts.suggest_quality report   # oracle + metrics per dataset
    python -m scripts.suggest_quality gate     # exit nonzero on regression (CI)
    python -m scripts.suggest_quality bless    # accept current as the baseline

Flags:
    --datasets a,b  filter to a subset of registered datasets
    --row-cap N     oracle row cap (default 20 000); ignored for full_scan datasets
    --native {0,1,auto}  GOLDENMATCH_NATIVE for this run
    --tolerance F   delta-F1 floor band for the gate (default 0.01)

Determinism: set before any goldenmatch import -- mirrors autoconfig_quality.
"""
from __future__ import annotations

import argparse
import gc
import math
import os
import sys
from pathlib import Path

# Polars CPU probe can hang on Windows; set before anything imports polars.
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_BASELINE = Path(__file__).resolve().parent / "baselines" / "scorecard.json"


# ── determinism helper ────────────────────────────────────────────────────────

def _pin_determinism(native: str | None = None) -> None:
    """Pin the determinism environment before any goldenmatch import.

    Mirrors autoconfig_quality's approach exactly:
    - GOLDENMATCH_AUTOCONFIG_MEMORY=0  disables cross-run memory (CI-safe)
    - PYTHONHASHSEED=0                 stable dict iteration
    - POLARS_SKIP_CPU_CHECK=1          avoid Windows WMI hang
    - GOLDENMATCH_NATIVE               passed as --native flag value, if given

    Called once at the top of main() before deferred imports.
    """
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
    if native is not None:
        os.environ["GOLDENMATCH_NATIVE"] = native


# ── run loop ──────────────────────────────────────────────────────────────────

def run(
    dataset_names: set[str] | None,
    row_cap: int | None,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Load the corpus, run the oracle for each dataset -> (results, skipped).

    Heavy imports are deferred so ``--native`` can set GOLDENMATCH_NATIVE
    before goldenmatch loads.  Same pattern as autoconfig_quality.run().

    Each result record carries the oracle output from evaluate_dataset plus
    the computed metrics (rank_corr, suggester_prec) ready for the table.
    """
    from scripts.suggest_quality.datasets import REGISTRY, effective_row_cap  # noqa: PLC0415
    from scripts.suggest_quality.metrics import rank_correlation, suggester_precision  # noqa: PLC0415
    from scripts.suggest_quality.oracle import evaluate_dataset  # noqa: PLC0415

    results: dict[str, dict] = {}
    skipped: dict[str, str] = {}

    for d in REGISTRY:
        if dataset_names and d.name not in dataset_names:
            continue
        try:
            loaded = d.loader()
        except Exception as e:  # loader failure -> skip with reason, never crash
            skipped[d.name] = f"loader_error: {e}"
            continue
        if loaded is None:
            skipped[d.name] = "absent"
            continue
        df, gt = loaded
        cap = effective_row_cap(d, row_cap)

        try:
            oracle_rec = evaluate_dataset(d.name, df, gt, row_cap=cap)
        except Exception as e:
            skipped[d.name] = f"oracle_error: {e}"
            del loaded, df, gt
            gc.collect()
            continue

        # Compute derived metrics
        lifts = oracle_rec.get("suggested_order_lifts", [])
        # Filter NaN lifts before passing to metrics (oracle may produce NaN
        # per-suggestion on error; exclude them from correlation/precision)
        clean_lifts = [x for x in lifts if not math.isnan(x)]

        rank_corr = rank_correlation(clean_lifts)
        sugg_prec = suggester_precision(clean_lifts)

        results[d.name] = {
            "kind": d.kind,
            # oracle output
            **oracle_rec,
            # derived metrics
            "rank_corr": rank_corr,
            "suggester_prec": sugg_prec,
        }
        del loaded, df, gt
        gc.collect()

    return results, skipped


# ── scorecard helpers ─────────────────────────────────────────────────────────

def _gather_meta() -> tuple[str, str]:
    """(native_version, git_sha) -- best-effort, never raises."""
    try:
        import goldenmatch_native  # noqa: PLC0415
        native_version = getattr(goldenmatch_native, "__version__", "unknown")
    except Exception:
        native_version = "absent"
    try:
        import subprocess  # noqa: PLC0415
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        git_sha = "unknown"
    return native_version, git_sha


def _fmt_f1(v: float) -> str:
    if math.isnan(v):
        return "   n/a"
    return f"{v:.4f}"


def _fmt_corr(v: float) -> str:
    if math.isnan(v):
        return "   n/a"
    return f"{v:+.3f}"


def _fmt_prec(v: float) -> str:
    return f"{v:.2f}"


def _render_report_table(
    results: dict[str, dict], skipped: dict[str, str]
) -> str:
    """Render the per-dataset metrics table."""
    lines: list[str] = []

    header = (
        f"  {'dataset':<30} {'kind':<7}  {'rows':<7}  "
        f"{'gt_pairs':<9}  {'base_f1':<8}  {'n_sugg':<6}  "
        f"{'rank_corr':<10}  {'sugg_prec':<9}  {'conv_f1':<8}  {'steps':<5}"
    )
    sep = "  " + "-" * (len(header) - 2)
    lines.append(header)
    lines.append(sep)

    for name, rec in results.items():
        if rec.get("error"):
            lines.append(
                f"  {name:<30} {'ERROR':<7}  {rec.get('error', '')}"
            )
            continue

        lines.append(
            f"  {name:<30} {rec['kind']:<7}  {rec['rows']:<7}  "
            f"{rec['gt_pairs']:<9}  {_fmt_f1(rec['baseline_f1']):<8}  "
            f"{rec['n_suggestions']:<6}  "
            f"{_fmt_corr(rec['rank_corr']):<10}  "
            f"{_fmt_prec(rec['suggester_prec']):<9}  "
            f"{_fmt_f1(rec['convergence_final_f1']):<8}  "
            f"{rec['convergence_steps']:<5}"
        )

    for name, reason in skipped.items():
        lines.append(f"  {name:<30} SKIPPED: {reason}")

    return "\n".join(lines)


def _compute_headline(results: dict[str, dict]) -> float:
    """Mean rank_correlation across datasets that have >= 2 suggestions."""
    corrs = [
        rec["rank_corr"]
        for rec in results.values()
        if not math.isnan(rec.get("rank_corr", float("nan")))
        and rec.get("n_suggestions", 0) >= 2
    ]
    if not corrs:
        return float("nan")
    return sum(corrs) / len(corrs)


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="suggest_quality")
    p.add_argument(
        "mode", nargs="?", default="report",
        choices=["report", "gate", "bless"],
    )
    p.add_argument("--datasets", default="", help="comma-separated dataset filter")
    p.add_argument("--row-cap", type=int, default=20_000, help="oracle row cap")
    p.add_argument(
        "--native", choices=["0", "1", "auto"], default=None,
        help="GOLDENMATCH_NATIVE for this run",
    )
    p.add_argument(
        "--tolerance", type=float, default=0.01,
        help="delta-F1 floor band for the gate",
    )
    args = p.parse_args(argv)

    # Pin determinism env BEFORE any goldenmatch import.
    _pin_determinism(args.native)

    names: set[str] | None = {s for s in args.datasets.split(",") if s} or None
    results, skipped = run(names, args.row_cap)
    native_version, git_sha = _gather_meta()

    if args.mode == "report":
        print("suggest_quality report")
        print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
        print()
        print(_render_report_table(results, skipped))
        print()

        headline = _compute_headline(results)
        n_with_gt = sum(1 for r in results.values() if r.get("gt_pairs", 0) > 0)
        native_ok = any(r.get("native_available", False) for r in results.values())

        print(f"  {len(results)} dataset(s) loaded, {len(skipped)} skipped")
        print(f"  {n_with_gt} dataset(s) with ground truth (F1 applicable)")
        if not native_ok:
            print("  native kernel absent -- suggestions not evaluated (install goldenmatch[native])")
        print(f"  suggester score (mean rank_corr): {_fmt_corr(headline)}")
        return 0

    if args.mode == "bless":
        return _cmd_bless(results, skipped, native_version, git_sha)

    if args.mode == "gate":
        return _cmd_gate(results, skipped, native_version, git_sha, args.tolerance)

    return 0  # unreachable


# ── bless ─────────────────────────────────────────────────────────────────────

def _build_scorecard(
    results: dict[str, dict],
    skipped: dict[str, str],
    native_version: str,
    git_sha: str,
) -> dict:
    """Assemble a stable, round-floated scorecard dict.

    Per-dataset record shape (mirrors the oracle output):
        kind, rows, gt_pairs, baseline_f1, n_suggestions,
        suggested_order_lifts, convergence_final_f1, convergence_steps,
        rank_corr, suggester_prec

    Floats are rounded to 6 decimal places so byte-stable re-runs produce
    the same JSON diff.
    """
    _PRECISION = 6

    def _round(v):
        if isinstance(v, float):
            if math.isnan(v):
                return None  # JSON has no NaN; None serializes as null
            return round(v, _PRECISION)
        if isinstance(v, dict):
            return {k: _round(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_round(x) for x in v]
        return v

    datasets_out: dict[str, dict] = {}
    for name, rec in results.items():
        datasets_out[name] = _round(rec)

    return {
        "meta": {
            "native_version": native_version,
            "git_sha": git_sha,
            "datasets_run": sorted(results.keys()),
            "datasets_skipped": skipped,
        },
        "datasets": datasets_out,
    }


def _dumps(scorecard: dict) -> str:
    """Serialize scorecard to a stable, human-readable JSON string."""
    import json  # noqa: PLC0415
    return json.dumps(scorecard, indent=2, sort_keys=True)


def _loads_baseline() -> dict:
    """Load the blessed baseline from disk, or return an empty baseline."""
    if not _BASELINE.exists():
        return {"datasets": {}, "meta": {}}
    try:
        import json  # noqa: PLC0415
        return json.loads(_BASELINE.read_text(encoding="utf-8"))
    except Exception:
        return {"datasets": {}, "meta": {}}


def _cmd_bless(
    results: dict[str, dict],
    skipped: dict[str, str],
    native_version: str,
    git_sha: str,
) -> int:
    """Write the current oracle results as the new blessed baseline."""
    scorecard = _build_scorecard(results, skipped, native_version, git_sha)
    _BASELINE.parent.mkdir(parents=True, exist_ok=True)
    _BASELINE.write_text(_dumps(scorecard), encoding="utf-8")
    print(f"suggest_quality bless: wrote {_BASELINE}")
    print(f"  {len(results)} dataset(s) blessed, {len(skipped)} skipped")
    for name, rec in results.items():
        bf = rec.get("baseline_f1")
        nc = rec.get("convergence_final_f1")
        print(
            f"  {name}: baseline_f1={_fmt_f1(bf if bf is not None else float('nan'))}  "
            f"conv_f1={_fmt_f1(nc if nc is not None else float('nan'))}  "
            f"n_sugg={rec.get('n_suggestions', 0)}"
        )
    return 0


# ── gate ──────────────────────────────────────────────────────────────────────

_GATE_TOLERANCES = {
    "rank_corr": 0.05,        # Spearman rank correlation
    "suggester_prec": 0.05,   # fraction of non-regressing suggestions
    "convergence_final_f1": 0.02,  # greedy-convergence final F1
}


def _cmd_gate(
    results: dict[str, dict],
    skipped: dict[str, str],
    native_version: str,
    git_sha: str,
    cli_tolerance: float,
) -> int:
    """Compare current oracle results against the blessed baseline.

    Exits 1 if ANY of:
      - zero datasets actually evaluated this run (gate certifies nothing)
      - a blessed dataset is MISSING from the current run (the dataset no
        longer evaluates — a regression that hides as "it didn't run")
      - any metric regressed beyond tolerance:
          rank_corr            (drops > 0.05)
          suggester_prec       (drops > 0.05)
          convergence_final_f1 (drops > 0.02)

    Datasets absent from the baseline are reported as NEW (informational —
    a brand-new dataset has no baseline to regress against).
    """
    baseline = _loads_baseline()
    base_datasets = baseline.get("datasets", {})

    # A gate that evaluated nothing must NOT be green: it would mask real
    # regressions. `results` holds only datasets that produced real metrics
    # this run (skipped/errored ones land in `skipped`).
    if not results:
        print("suggest_quality gate")
        print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
        print(f"  baseline={_BASELINE}")
        print()
        print("  ERROR: gate evaluated 0 datasets; cannot certify.")
        if skipped:
            print("  skipped: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()))
        return 1

    _COL_W = 32
    _MET_W = 22
    _VAL_W = 8

    header = (
        f"  {'dataset':<{_COL_W}} {'metric':<{_MET_W}} "
        f"{'baseline':>{_VAL_W}}  {'current':>{_VAL_W}}  {'delta':>8}  status"
    )
    sep = "  " + "-" * (len(header) - 2)

    rows: list[tuple[str, str, str, str, str, str]] = []  # (ds, metric, base, cur, delta, status)

    for name, rec in results.items():
        b = base_datasets.get(name)
        for metric, tol in _GATE_TOLERANCES.items():
            cur_raw = rec.get(metric)
            if cur_raw is None or (isinstance(cur_raw, float) and math.isnan(cur_raw)):
                continue  # not available this run -> skip

            cur = float(cur_raw)

            if b is None:
                # New dataset not in baseline
                rows.append((
                    name, metric,
                    "n/a", f"{cur:+.4f}", "  n/a", "NEW",
                ))
                continue

            base_raw = b.get(metric)
            if base_raw is None:
                rows.append((name, metric, "n/a", f"{cur:+.4f}", "  n/a", "NEW"))
                continue

            base = float(base_raw) if base_raw is not None else float("nan")
            if math.isnan(base):
                rows.append((name, metric, "n/a", f"{cur:+.4f}", "  n/a", "NEW"))
                continue

            delta = cur - base
            if delta < -tol:
                status = "FAIL"
            else:
                status = "OK"

            rows.append((
                name, metric,
                f"{base:+.4f}", f"{cur:+.4f}", f"{delta:+.4f}", status,
            ))

    # Datasets in baseline but absent from current run -> gate FAIL.
    for name in base_datasets:
        if name not in results:
            rows.append((name, "*", "present", "absent", "  n/a", "MISSING"))

    # Render table
    print("suggest_quality gate")
    print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
    print(f"  baseline={_BASELINE}")
    print()
    print(header)
    print(sep)
    for ds, met, base_s, cur_s, delta_s, status in rows:
        mark = {"FAIL": "x", "OK": ".", "NEW": "+", "MISSING": "x"}.get(status, "?")
        print(
            f"  {ds:<{_COL_W}} {met:<{_MET_W}} "
            f"{base_s:>{_VAL_W}}  {cur_s:>{_VAL_W}}  {delta_s:>8}  {mark} ({status})"
        )
    if not rows:
        print("  (no comparable datasets — baseline may be empty)")
    print()

    n_fail = sum(1 for *_, s in rows if s == "FAIL")
    n_ok = sum(1 for *_, s in rows if s == "OK")
    n_new = sum(1 for *_, s in rows if s == "NEW")
    n_missing = sum(1 for *_, s in rows if s == "MISSING")

    # A blessed dataset that no longer evaluates is a regression too: a real
    # break can surface as "the dataset stopped running", not just a metric
    # drop. NEW datasets stay informational (no baseline to regress against).
    verdict = "FAIL" if (n_fail > 0 or n_missing > 0) else "PASS"
    print(
        f"  verdict: {verdict}  "
        f"({n_ok} ok, {n_fail} fail, {n_new} new, {n_missing} missing, "
        f"{len(skipped)} skipped)"
    )
    if skipped:
        print("  skipped: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()))

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
