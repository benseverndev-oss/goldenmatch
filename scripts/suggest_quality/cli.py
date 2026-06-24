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
        print("suggest_quality bless: not implemented until Task 16")
        print("(Task 16 will wire the oracle, build the scorecard, and commit the baseline.)")
        return 0

    if args.mode == "gate":
        print("suggest_quality gate: not implemented until Task 16")
        print("(Task 16 adds the CI gate against the bless'd baseline.)")
        # Exit 0 (never fail) until the gate is implemented.
        return 0

    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
