"""Config-suggestion quality harness CLI.

    python -m scripts.suggest_quality report      # oracle + metrics per dataset
    python -m scripts.suggest_quality gate        # exit nonzero on regression (CI)
    python -m scripts.suggest_quality bless       # accept current as the baseline
    python -m scripts.suggest_quality gym         # catalog board (live vs raw recovery)
    python -m scripts.suggest_quality gym-bless   # write gym_scorecard.json baseline
    python -m scripts.suggest_quality gym-gate    # gate gym recovery vs baseline (CI)

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
_GYM_BASELINE = Path(__file__).resolve().parent / "baselines" / "gym_scorecard.json"

# Tolerance for gym recovery% non-regression (absolute drop allowed).
RECOVERY_GATE_TOL: float = 0.05


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
    from scripts.suggest_quality.metrics import (  # noqa: PLC0415
        rank_correlation,
        suggester_precision,
    )
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
        choices=["report", "gate", "bless", "gym", "gym-bless", "gym-gate", "bakeoff"],
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

    # Gym modes use run_catalog (not the oracle), so branch early.
    if args.mode in ("gym", "gym-bless", "gym-gate"):
        native_version, git_sha = _gather_meta()
        return _run_gym_mode(args.mode, names, native_version, git_sha)

    if args.mode == "bakeoff":
        native_version, git_sha = _gather_meta()
        return _run_bakeoff_mode(names, native_version, git_sha)

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

# (dataset, metric) pairs that are REPORTED but NOT gating. A metric here shows
# in the table with status ADVISORY and never flips the verdict.
#
# ncvr_synthetic/suggester_prec: ncvr_synthetic is large enough that auto-config
# commits a best-effort RED config under a WALL-CLOCK budget (stop_reason
# BUDGET_TIME), so WHICH config commits — and therefore whether the suggester's
# fix scores prec 1.0 (no/clean suggestion) or 0.0 (one spurious suggestion) —
# varies run-to-run with CI speed, NOT with the code. Verified: across recent
# runs on a byte-identical kernel it flips 1.0<->0.0 (~1 in 6). A wall-clock-
# non-deterministic value can't be a hard gate; pinning a config doesn't help
# (the committed config is RED either way). Keep it visible, don't gate on it.
_GATE_ADVISORY = {
    ("ncvr_synthetic", "suggester_prec"),
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
            if (name, metric) in _GATE_ADVISORY:
                status = "ADVISORY"  # reported, never gates (non-deterministic)
            elif delta < -tol:
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
        mark = {"FAIL": "x", "OK": ".", "NEW": "+", "MISSING": "x", "ADVISORY": "~"}.get(status, "?")
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
    n_advisory = sum(1 for *_, s in rows if s == "ADVISORY")

    # A blessed dataset that no longer evaluates is a regression too: a real
    # break can surface as "the dataset stopped running", not just a metric
    # drop. NEW datasets stay informational (no baseline to regress against).
    verdict = "FAIL" if (n_fail > 0 or n_missing > 0) else "PASS"
    print(
        f"  verdict: {verdict}  "
        f"({n_ok} ok, {n_fail} fail, {n_new} new, {n_missing} missing, "
        f"{n_advisory} advisory, {len(skipped)} skipped)"
    )
    if skipped:
        print("  skipped: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()))

    return 0 if verdict == "PASS" else 1


# ── gym (catalog board) ───────────────────────────────────────────────────────

def _run_gym_mode(
    mode: str,
    dataset_names: set[str] | None,
    native_version: str,
    git_sha: str,
) -> int:
    """Dispatch gym / gym-bless / gym-gate after deferred imports."""
    # Deferred imports -- native env is already pinned.
    from scripts.suggest_quality.datasets import REGISTRY  # noqa: PLC0415
    from scripts.suggest_quality.gym import run_catalog  # noqa: PLC0415
    from scripts.suggest_quality.perturbations import CATALOG  # noqa: PLC0415

    # Guard: native suggest_config must be available.  run_catalog will catch
    # individual errors, but an upfront check gives a cleaner message.
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415
        _mod = native_module()
        if _mod is None or not hasattr(_mod, "suggest_config"):
            print(
                "gym requires the native suggest_config kernel.\n"
                "Build it:  uv run python scripts/build_native.py\n"
                "  or install goldenmatch[native]"
            )
            return 1
    except Exception as exc:
        print(f"gym: native loader error: {exc}")
        return 1

    # Select datasets with GT (anchors that return empty gt skip automatically
    # inside run_catalog; we filter by name here if --datasets was set).
    datasets = [
        d for d in REGISTRY
        if dataset_names is None or d.name in dataset_names
    ]

    records = run_catalog(datasets, CATALOG)

    if mode == "gym":
        return _cmd_gym(records, native_version, git_sha)
    if mode == "gym-bless":
        return _cmd_gym_bless(records, native_version, git_sha)
    if mode == "gym-gate":
        return _cmd_gym_gate(records, native_version, git_sha)
    return 0  # unreachable


def _run_bakeoff_mode(dataset_names: set[str] | None, native_version: str, git_sha: str) -> int:
    """Run the verify-gate proxy bake-off and print the per-proxy table + winner."""
    from scripts.suggest_quality.bakeoff import (  # noqa: PLC0415
        build_proxies,
        run_bakeoff_catalog,
        select_best,
    )
    from scripts.suggest_quality.datasets import REGISTRY  # noqa: PLC0415
    from scripts.suggest_quality.perturbations import CATALOG  # noqa: PLC0415

    # Same native guard as _run_gym_mode.
    try:
        from goldenmatch.core._native_loader import native_module  # noqa: PLC0415
        _mod = native_module()
        if _mod is None or not hasattr(_mod, "suggest_config"):
            print("bakeoff requires the native suggest_config kernel.\n"
                  "Build it:  uv run python scripts/build_native.py")
            return 1
    except Exception as exc:
        print(f"bakeoff: native loader error: {exc}")
        return 1

    from scripts.suggest_quality.bakeoff import harmful_accept_rows  # noqa: PLC0415

    datasets = [d for d in REGISTRY if dataset_names is None or d.name in dataset_names]
    proxies = build_proxies()
    rows = run_bakeoff_catalog(datasets, CATALOG, proxies)
    winner, table = select_best(rows)

    # Which perturbation names are the deliberately-adversarial traps (a harmful
    # accept on one of these is far less damning than on a real recovery pair).
    _ADVERSARIAL = {"near_valley_threshold", "over_merge_bait"}

    print("verify-gate proxy bake-off")
    print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
    n_pairs = len({(r['dataset'], r['perturbation']) for r in rows})
    print(f"  {len(rows)} (fix x proxy) rows over {n_pairs} damaging pairs")
    print()
    hdr = (f"  {'proxy':<34} {'accepted':>8} {'acc_harm':>8} {'real_wins':>9} "
           f"{'precision':>9} {'recall':>7} {'net_f1':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for name in sorted(table):
        s = table[name]
        rec = "   n/a" if math.isnan(s["recall"]) else f"{s['recall']:6.3f}"
        flag = "  <-- DISQUALIFIED (accepts harmful)" if s["n_accepted_harmful"] else ""
        print(f"  {name:<34} {s['n_accepted']:>8} {s['n_accepted_harmful']:>8} "
              f"{s['n_real_wins']:>9} {s['precision_safe']:>9.3f} {rec:>7} "
              f"{s['net_f1_delta']:>+8.4f}{flag}")
    print()

    # Per-proxy harmful-accept detail: trap vs real pair is the load-bearing
    # distinction for whether a disqualified proxy is actually shippable.
    harmful = harmful_accept_rows(rows)
    if harmful:
        print("  harmful accepts (proxy accepted a fix that LOWERED F1):")
        for r in sorted(harmful, key=lambda x: (x["proxy"], x["dataset"], x["perturbation"])):
            tag = "ADVERSARIAL-TRAP" if r["perturbation"] in _ADVERSARIAL else "REAL-PAIR"
            print(f"    {r['proxy']:<34} {r['dataset']}/{r['perturbation']} "
                  f"f1_delta={r['f1_delta']:+.4f}  [{tag}]")
        print()

    if winner is None:
        print("  WINNER (zero accepted-harmful, max recall): none -- consider Phase B.")
    else:
        print(f"  WINNER (zero accepted-harmful, max recall): {winner}")
    # Also surface the best-by-net-value proxy, which may differ from the strict winner.
    best_net = max(table, key=lambda nm: table[nm]["net_f1_delta"])
    print(f"  BEST net_f1_delta: {best_net}  (net={table[best_net]['net_f1_delta']:+.4f}, "
          f"acc_harm={table[best_net]['n_accepted_harmful']})")
    return 0


def _fmt_pct(v) -> str:
    """Format a recovery% (0-1 float) as a percentage string, or dash."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "    -"
    return f"{v * 100:5.1f}%"


def _fmt_bool(v) -> str:
    return "yes" if v else "no"


def _cmd_gym(records: list[dict], native_version: str, git_sha: str) -> int:
    """Print the catalog board: one row per (dataset, perturbation)."""
    ok_records = [r for r in records if r.get("status") == "ok"]
    built_ok = [r for r in ok_records if r.get("builds_on_existing_rule")]
    standing = [r for r in records if not r.get("builds_on_existing_rule", False)]

    print("gym catalog board")
    print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
    print()

    # ── per-row table ─────────────────────────────────────────────────────────
    _DS_W = 22
    _PT_W = 25
    _ST_W = 9
    _F1_W = 7
    _PC_W = 9
    _RF_W = 5
    _GP_W = 8

    header = (
        f"  {'dataset':<{_DS_W}} {'perturbation':<{_PT_W}} {'status':<{_ST_W}}"
        f"  {'ceil':<{_F1_W}} {'degraded':<{_F1_W}}"
        f"  {'rec%_LIVE':<{_PC_W}} {'fired':<{_RF_W}}"
        f"  {'rec%_RAW':<{_PC_W}} {'fired':<{_RF_W}}"
        f"  {'gap':>{_GP_W}}"
    )
    sep = "  " + "-" * (len(header) - 2)
    print(header)
    print(sep)

    for r in records:
        ds = r.get("dataset", "")
        name = r.get("name", "")
        status = r.get("status", "?")

        if status == "ok":
            ceil_s = f"{r['f1_ceiling']:.4f}" if r.get("f1_ceiling") is not None else "   n/a"
            deg_s = f"{r['f1_degraded']:.4f}" if r.get("f1_degraded") is not None else "   n/a"
            rec_live = _fmt_pct(r.get("recovery_pct_live"))
            fired_live = _fmt_bool(r.get("expected_rule_fired_live"))
            rec_raw = _fmt_pct(r.get("recovery_pct_raw"))
            fired_raw = _fmt_bool(r.get("expected_rule_fired_raw"))
            gap_v = r.get("verification_gap")
            gap_s = _fmt_pct(gap_v) if gap_v is not None else "    -"
            print(
                f"  {ds:<{_DS_W}} {name:<{_PT_W}} {status:<{_ST_W}}"
                f"  {ceil_s:<{_F1_W}} {deg_s:<{_F1_W}}"
                f"  {rec_live:<{_PC_W}} {fired_live:<{_RF_W}}"
                f"  {rec_raw:<{_PC_W}} {fired_raw:<{_RF_W}}"
                f"  {gap_s:>{_GP_W}}"
            )
        else:
            dashes = "-" * 5
            print(
                f"  {ds:<{_DS_W}} {name:<{_PT_W}} {status:<{_ST_W}}"
                f"  {dashes:<{_F1_W}} {dashes:<{_F1_W}}"
                f"  {dashes:<{_PC_W}} {dashes:<{_RF_W}}"
                f"  {dashes:<{_PC_W}} {dashes:<{_RF_W}}"
                f"  {dashes:>{_GP_W}}"
            )

    print()

    # ── per-rule rollup ───────────────────────────────────────────────────────
    rule_records: dict[str, list[dict]] = {}
    for r in ok_records:
        rule = r.get("expected_rule") or "(none)"
        rule_records.setdefault(rule, []).append(r)

    if rule_records:
        print("  per-rule rollup (ok records only):")
        rollup_header = (
            f"    {'rule':<28} {'n':>3}  {'mean_rec%_LIVE':>14}  {'mean_rec%_RAW':>13}"
        )
        print(rollup_header)
        print("    " + "-" * (len(rollup_header) - 4))
        for rule, recs in sorted(rule_records.items()):
            live_vals = [r["recovery_pct_live"] for r in recs if r.get("recovery_pct_live") is not None]
            raw_vals = [r["recovery_pct_raw"] for r in recs if r.get("recovery_pct_raw") is not None]
            mean_live = sum(live_vals) / len(live_vals) if live_vals else float("nan")
            mean_raw = sum(raw_vals) / len(raw_vals) if raw_vals else float("nan")
            print(
                f"    {rule:<28} {len(recs):>3}  {_fmt_pct(mean_live):>14}  {_fmt_pct(mean_raw):>13}"
            )
        print()

    # ── headlines ─────────────────────────────────────────────────────────────
    if built_ok:
        live_vals = [r["recovery_pct_live"] for r in built_ok if r.get("recovery_pct_live") is not None]
        raw_vals = [r["recovery_pct_raw"] for r in built_ok if r.get("recovery_pct_raw") is not None]
        headline_live = sum(live_vals) / len(live_vals) if live_vals else float("nan")
        headline_raw = sum(raw_vals) / len(raw_vals) if raw_vals else float("nan")
        gap = headline_raw - headline_live if not (math.isnan(headline_live) or math.isnan(headline_raw)) else float("nan")
        print(f"  gym score (live) = {_fmt_pct(headline_live)}  [built-rule perturbations, n={len(built_ok)}]")
        print(f"  gym score (raw)  = {_fmt_pct(headline_raw)}")
        gap_pct = f"{gap * 100:+.1f}pp" if not math.isnan(gap) else "n/a"
        print(f"  verification gap = {gap_pct}  (raw - live; gap = how much self-verify suppresses correct fixing)")
    else:
        print("  gym score: no built-rule ok records to score")

    if standing:
        print()
        print("  standing targets (rule not built yet):")
        for r in standing:
            ds = r.get("dataset", "")
            name = r.get("name", "")
            status = r.get("status", "?")
            expected = r.get("expected_rule") or "(none)"
            print(f"    {ds}/{name}  expected_rule={expected}  status={status}")

    print()
    return 0


# ── gym-bless ─────────────────────────────────────────────────────────────────

def _build_gym_scorecard(
    records: list[dict],
    native_version: str,
    git_sha: str,
) -> dict:
    """Build the gym scorecard dict from run_catalog output."""
    _PRECISION = 6

    def _rf(v):
        """Round a float, converting NaN -> None (JSON-safe)."""
        if isinstance(v, float):
            return None if math.isnan(v) else round(v, _PRECISION)
        return v

    ok_records = [r for r in records if r.get("status") == "ok"]
    built_ok = [r for r in ok_records if r.get("builds_on_existing_rule")]

    # Per-(dataset, perturbation) entries.
    pairs: dict[str, dict] = {}
    for r in records:
        key = f"{r.get('dataset', '')}/{r.get('name', '')}"
        pairs[key] = {
            "status": r.get("status"),
            "builds_on_existing_rule": r.get("builds_on_existing_rule"),
            "expected_rule": r.get("expected_rule"),
            "recovery_pct_live": _rf(r.get("recovery_pct_live")),
            "recovery_pct_raw": _rf(r.get("recovery_pct_raw")),
            "expected_rule_fired_live": r.get("expected_rule_fired_live"),
            "expected_rule_fired_raw": r.get("expected_rule_fired_raw"),
        }

    # Per-rule rollup.
    rule_rollup: dict[str, dict] = {}
    rule_records: dict[str, list[dict]] = {}
    for r in ok_records:
        rule = r.get("expected_rule") or "(none)"
        rule_records.setdefault(rule, []).append(r)
    for rule, recs in rule_records.items():
        live_vals = [r["recovery_pct_live"] for r in recs if r.get("recovery_pct_live") is not None]
        raw_vals = [r["recovery_pct_raw"] for r in recs if r.get("recovery_pct_raw") is not None]
        mean_live = sum(live_vals) / len(live_vals) if live_vals else float("nan")
        mean_raw = sum(raw_vals) / len(raw_vals) if raw_vals else float("nan")
        rule_rollup[rule] = {
            "n_ok": len(recs),
            "mean_recovery_pct_live": _rf(mean_live),
            "mean_recovery_pct_raw": _rf(mean_raw),
        }

    # Headlines.
    if built_ok:
        live_vals = [r["recovery_pct_live"] for r in built_ok if r.get("recovery_pct_live") is not None]
        raw_vals = [r["recovery_pct_raw"] for r in built_ok if r.get("recovery_pct_raw") is not None]
        headline_live: float | None = _rf(sum(live_vals) / len(live_vals)) if live_vals else None
        headline_raw: float | None = _rf(sum(raw_vals) / len(raw_vals)) if raw_vals else None
    else:
        headline_live = None
        headline_raw = None

    return {
        "meta": {
            "native_version": native_version,
            "git_sha": git_sha,
            "n_records": len(records),
            "n_ok": len(ok_records),
            "n_built_ok": len(built_ok),
        },
        "headline_live": headline_live,
        "headline_raw": headline_raw,
        "pairs": pairs,
        "rule_rollup": rule_rollup,
    }


def _cmd_gym_bless(records: list[dict], native_version: str, git_sha: str) -> int:
    """Write gym_scorecard.json as the new blessed gym baseline."""
    import json  # noqa: PLC0415
    scorecard = _build_gym_scorecard(records, native_version, git_sha)
    _GYM_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    _GYM_BASELINE.write_text(json.dumps(scorecard, indent=2, sort_keys=True), encoding="utf-8")

    ok_n = scorecard["meta"]["n_ok"]
    built_n = scorecard["meta"]["n_built_ok"]
    hl_live = scorecard.get("headline_live")
    hl_raw = scorecard.get("headline_raw")
    hl_live_s = _fmt_pct(hl_live) if hl_live is not None else "n/a"
    hl_raw_s = _fmt_pct(hl_raw) if hl_raw is not None else "n/a"

    print(f"gym-bless: wrote {_GYM_BASELINE}")
    print(f"  {len(records)} pair(s), {ok_n} ok, {built_n} built-rule ok")
    print(f"  headline_live={hl_live_s}  headline_raw={hl_raw_s}")
    return 0


# ── gym-gate ──────────────────────────────────────────────────────────────────

def _loads_gym_baseline() -> dict:
    """Load the blessed gym baseline, or empty."""
    if not _GYM_BASELINE.exists():
        return {}
    try:
        import json  # noqa: PLC0415
        return json.loads(_GYM_BASELINE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cmd_gym_gate(records: list[dict], native_version: str, git_sha: str) -> int:
    """Gate gym recovery% against the blessed gym_scorecard.json.

    Fails (exit 1) when:
      - zero ok records this run (gate certifies nothing)
      - a blessed built-rule pair is MISSING from this run (absent for a reason
        OTHER than a degenerate ceiling -- see below)
      - any built-rule pair's recovery_pct_live/raw drops > RECOVERY_GATE_TOL vs blessed
      - the COMMON-population headline_live/raw drops > RECOVERY_GATE_TOL vs blessed

    NEW pairs (in this run but not in baseline) are informational. A blessed pair
    whose dataset was SKIPPED this run for a degenerate zero-config ceiling (#1620)
    is advisory, not a failure -- recovery% is unmeasurable against a broken
    ceiling, so re-failing the guard's own deliberate skip is wrong (re-bless to
    drop it). Headlines compare over the COMMON measurable population so a dataset
    leaving the set (degenerate) can't phantom-shift the mean and fail the gate.
    """
    baseline = _loads_gym_baseline()
    base_pairs: dict[str, dict] = baseline.get("pairs", {})
    # NB: headlines are recomputed over the COMMON measurable population below
    # (not read from the stored baseline scalars), so a dataset dropping out this
    # run can't phantom-shift the mean.

    ok_records = [r for r in records if r.get("status") == "ok"]

    print("gym-gate")
    print(f"  native={native_version}  sha={git_sha[:12] if git_sha != 'unknown' else 'unknown'}")
    print(f"  baseline={_GYM_BASELINE}")
    print()

    # Zero-eval guard.
    if not ok_records:
        print("  ERROR: gym-gate evaluated 0 ok records; cannot certify.")
        return 1

    # Build current pair lookup.
    cur_pairs: dict[str, dict] = {}
    for r in records:
        key = f"{r.get('dataset', '')}/{r.get('name', '')}"
        cur_pairs[key] = r

    # Datasets skipped THIS run for a degenerate zero-config ceiling (#1620): the
    # gym literally cannot measure recovery% there (recovery is scored against the
    # ceiling), so a blessed pair going absent for THIS reason is an advisory, not
    # a MISSING failure -- that just re-fails the guard's own deliberate skip. A
    # pair absent for ANY OTHER reason (dataset erroring / genuinely removed) stays
    # MISSING and still fails.
    degenerate_skipped: set[str] = {
        r.get("dataset") for r in records
        if r.get("status") == "skipped_degenerate_ceiling"
    }

    _COL_W = 42
    _MET_W = 22
    _VAL_W = 8
    header = (
        f"  {'pair':<{_COL_W}} {'metric':<{_MET_W}} "
        f"{'baseline':>{_VAL_W}}  {'current':>{_VAL_W}}  {'delta':>8}  status"
    )
    sep = "  " + "-" * (len(header) - 2)
    print(header)
    print(sep)

    rows: list[tuple[str, str, str, str, str, str]] = []

    # Per-pair checks (built-rule pairs only for gate failures).
    for key, bpair in base_pairs.items():
        if not bpair.get("builds_on_existing_rule"):
            continue  # unbuilt-rule pairs are informational
        if bpair.get("status") != "ok":
            continue  # only ok pairs gated

        cur = cur_pairs.get(key)
        if cur is None or cur.get("status") != "ok":
            dataset_name = key.split("/", 1)[0]
            if dataset_name in degenerate_skipped:
                # Unmeasurable this run (degenerate ceiling) -> advisory, not a fail.
                rows.append((key, "*", "ok", "degenerate ceiling", "  n/a", "SKIPPED"))
            else:
                rows.append((key, "*", "ok", "absent/non-ok", "  n/a", "MISSING"))
            continue

        for metric_key, label in [
            ("recovery_pct_live", "recovery_pct_live"),
            ("recovery_pct_raw", "recovery_pct_raw"),
        ]:
            base_v = bpair.get(metric_key)
            cur_v = cur.get(metric_key)
            if base_v is None or cur_v is None:
                continue
            delta = float(cur_v) - float(base_v)
            if delta < -RECOVERY_GATE_TOL:
                status = "FAIL"
            else:
                status = "OK"
            rows.append((
                key, label,
                f"{float(base_v):.4f}", f"{float(cur_v):.4f}", f"{delta:+.4f}", status,
            ))

    # New pairs (informational).
    for key, r in cur_pairs.items():
        if key not in base_pairs and r.get("status") == "ok" and r.get("builds_on_existing_rule"):
            for label in ("recovery_pct_live", "recovery_pct_raw"):
                v = r.get(label)
                if v is not None:
                    rows.append((key, label, "n/a", f"{float(v):.4f}", "  n/a", "NEW"))

    # Headline checks.
    #
    # The blessed headline_live/raw are means over the baseline's built-rule ok
    # pairs. If a dataset drops out this run (degenerate ceiling) the global mean
    # is recomputed over a DIFFERENT population, so a direct scalar-vs-scalar
    # compare phantom-fails (the ncvr_synthetic drop shifted headline_raw 0.75 ->
    # 0.67 with zero per-pair regression). Compare the headline over the COMMON
    # measurable population instead -- built-rule pairs that are ok in BOTH the
    # baseline and this run -- so a dataset leaving the set can't move the delta,
    # while a genuine drop on a still-measured dataset is fully caught.
    common_keys = [
        key for key, bpair in base_pairs.items()
        if bpair.get("builds_on_existing_rule") and bpair.get("status") == "ok"
        and key.split("/", 1)[0] not in degenerate_skipped
        and (cur_pairs.get(key) or {}).get("status") == "ok"
    ]

    def _mean_over(source: dict, metric: str) -> float | None:
        vals = [float(source[k][metric]) for k in common_keys
                if source.get(k, {}).get(metric) is not None]
        return sum(vals) / len(vals) if vals else None

    population_changed = any(
        key.split("/", 1)[0] in degenerate_skipped
        for key, bpair in base_pairs.items()
        if bpair.get("builds_on_existing_rule") and bpair.get("status") == "ok"
    )

    for label, metric in [("headline_live", "recovery_pct_live"),
                          ("headline_raw", "recovery_pct_raw")]:
        base_v = _mean_over(base_pairs, metric)
        cur_v = _mean_over(cur_pairs, metric)
        if base_v is None or cur_v is None:
            if cur_v is not None:
                rows.append(("(headline)", label, "n/a", f"{float(cur_v):.4f}", "  n/a", "NEW"))
            continue
        delta = cur_v - base_v
        status = "FAIL" if delta < -RECOVERY_GATE_TOL else "OK"
        note = " (common set)" if population_changed else ""
        rows.append((
            "(headline)", label + note,
            f"{base_v:.4f}", f"{cur_v:.4f}", f"{delta:+.4f}", status,
        ))

    for pair_key, met, base_s, cur_s, delta_s, status in rows:
        mark = {"FAIL": "x", "OK": ".", "NEW": "+", "MISSING": "x",
                "SKIPPED": "~"}.get(status, "?")
        print(
            f"  {pair_key:<{_COL_W}} {met:<{_MET_W}} "
            f"{base_s:>{_VAL_W}}  {cur_s:>{_VAL_W}}  {delta_s:>8}  {mark} ({status})"
        )

    if not rows:
        print("  (no comparable pairs -- baseline may be empty)")
    print()

    n_fail = sum(1 for *_, s in rows if s == "FAIL")
    n_ok = sum(1 for *_, s in rows if s == "OK")
    n_new = sum(1 for *_, s in rows if s == "NEW")
    n_missing = sum(1 for *_, s in rows if s == "MISSING")
    n_skipped = sum(1 for *_, s in rows if s == "SKIPPED")

    # SKIPPED is advisory (a blessed dataset became degenerate this run -- recovery
    # is unmeasurable, not regressed); only real FAILs and genuine MISSINGs gate.
    verdict = "FAIL" if (n_fail > 0 or n_missing > 0) else "PASS"
    print(
        f"  verdict: {verdict}  "
        f"({n_ok} ok, {n_fail} fail, {n_new} new, {n_missing} missing, "
        f"{n_skipped} skipped)"
    )
    if n_skipped:
        print(f"  note: {n_skipped} blessed pair(s) SKIPPED (degenerate ceiling this "
              f"run) -- advisory; re-bless to drop them from the baseline.")

    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
