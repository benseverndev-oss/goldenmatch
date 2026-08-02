"""A/B-a-lever gate — the FS/Lever-Enablement regression gate (Phase 0).

Measures the F1 / precision / recall delta of a single auto-config lever across
the locally-available ``bench_er_headtohead`` panel datasets, and reports a
per-dataset PASS/FAIL: a lever may only flip its default when NO panel dataset
regresses beyond ``--tol``.

The lever is toggled via an environment variable read lazily by the engine
(e.g. ``GOLDENMATCH_FS_DOMAIN_COMPARATORS``), so both arms run in one process by
flipping ``os.environ`` between runs. Zero-config: each dataset goes through
``auto_configure_probabilistic_df`` + ``dedupe_df`` (the real default FS path),
scored against committed ground truth via ``evaluate_clusters``.

Usage:
    python -m scripts.bench_er_headtohead.ab_lever \
        --env GOLDENMATCH_FS_DOMAIN_COMPARATORS --off 0 --on 1
    # optional: --datasets historical_50k,febrl3  --tol 0.005

Design spec: docs/superpowers/specs/2026-08-01-fs-lever-enablement-design.md
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# The real F1 panel (anchor shapes with no positive ground-truth pairs are
# excluded — they gate structure, not F1). Skipped automatically when a
# dataset's optional dep / vendored file is absent (loader returns None).
#
# The five real datasets are all 0.50-OPTIMAL (FS at ceiling — every cheap lever
# declined on them). The two synthetic over-merge shapes cover the failure mode
# the real panel structurally lacks: household_hardneg (MODERATE surname
# over-merge) + cotenant_hardneg (SEVERE address over-merge), where the fixed
# 0.50 cutoff over-merges and a lever (the threshold-refit loop) can actually
# move F1. Including them means a lever A/B is measured on both the at-ceiling
# regime (must-not-regress) AND the has-headroom regime (can-it-win).
_PANEL = [
    "person", "febrl3", "ncvr_synthetic", "dblp_acm", "historical_50k",
    "household_hardneg", "cotenant_hardneg",
]


def _load(name: str):
    """Load a panel dataset.

    Raises ``KeyError`` on an UNKNOWN dataset name (a typo / config error the
    gate must NOT silently swallow into a smaller panel). Returns ``None`` only
    when a KNOWN loader reports the dataset is unavailable (optional dep or
    vendored file absent -> that dataset is skipped, surfaced by the caller). A
    real loader exception propagates (a genuine bug, not an expected skip)."""
    from scripts.autoconfig_quality import datasets as D

    fn = getattr(D, f"_{name}", None)
    if fn is None:
        raise KeyError(
            f"unknown panel dataset {name!r} "
            f"(no loader _{name} in scripts.autoconfig_quality.datasets)"
        )
    return fn()  # loader returns None when its dep/vendored file is absent -> skip


def _f1(name: str) -> dict | None:
    """Run zero-config FS dedupe on one dataset, return the F1 summary (or None
    to skip). Reads the lever from the CURRENT os.environ."""
    loaded = _load(name)
    if loaded is None:
        return None
    df, gt = loaded
    if not gt:  # anchor shape, no positive pairs -> not an F1 dataset
        return None
    import goldenmatch
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.evaluate import evaluate_clusters

    cfg = auto_configure_probabilistic_df(df)
    t0 = time.perf_counter()
    res = goldenmatch.dedupe_df(df, config=cfg)
    wall = time.perf_counter() - t0
    ev = evaluate_clusters(res.clusters, gt).summary()
    return {"f1": ev["f1"], "p": ev["precision"], "r": ev["recall"], "wall": wall}


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B one auto-config lever across the F1 panel.")
    ap.add_argument("--env", required=True, help="env var toggling the lever")
    ap.add_argument("--off", default="0", help="OFF value (baseline)")
    ap.add_argument("--on", default="1", help="ON value (candidate)")
    ap.add_argument("--datasets", default=",".join(_PANEL), help="comma list")
    ap.add_argument("--tol", type=float, default=0.005,
                    help="max allowed per-dataset F1 regression before FAIL")
    args = ap.parse_args()

    # Isolate the measurement from cross-run auto-config memory.
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]

    rows: list[tuple[str, dict, dict]] = []
    skipped: list[str] = []
    for name in datasets:
        # KeyError (unknown name) intentionally propagates -- a typo must not
        # silently shrink the panel. A None result = a known-unavailable dataset.
        os.environ[args.env] = args.off
        off = _f1(name)
        if off is None:
            skipped.append(name)
            continue
        os.environ[args.env] = args.on
        on = _f1(name)
        assert on is not None, f"{name}: measurable OFF but unmeasurable ON"
        rows.append((name, off, on))

    if skipped:
        print(f"[skipped] {len(skipped)} unavailable dataset(s): {', '.join(skipped)}",
              file=sys.stderr)
    # A regression gate that measured NOTHING must FAIL, never PASS -- an empty
    # panel is a broken environment, not a clean bill of health.
    if not rows:
        print(f"\nGATE: FAIL — 0 datasets measured (requested {len(datasets)}, "
              f"all unavailable). A gate that measures nothing cannot PASS.")
        return 1

    print(f"\nA/B lever: {args.env}  (OFF={args.off}  ON={args.on})  tol={args.tol}")
    print(f"{'dataset':18s} {'F1 off':>8s} {'F1 on':>8s} {'dF1':>8s} "
          f"{'P off':>7s} {'P on':>7s} {'R off':>7s} {'R on':>7s}  verdict")
    worst = 0.0
    any_regress = False
    for name, off, on in rows:
        d = on["f1"] - off["f1"]
        worst = min(worst, d)
        regress = d < -args.tol
        any_regress = any_regress or regress
        verdict = "REGRESS" if regress else ("win" if d > args.tol else "flat")
        print(f"{name:18s} {off['f1']:8.4f} {on['f1']:8.4f} {d:+8.4f} "
              f"{off['p']:7.3f} {on['p']:7.3f} {off['r']:7.3f} {on['r']:7.3f}  {verdict}")

    print(f"\nGATE: {'FAIL' if any_regress else 'PASS'} "
          f"(worst dF1 {worst:+.4f}, tol {args.tol}); {len(rows)} datasets measured")
    return 1 if any_regress else 0


if __name__ == "__main__":
    raise SystemExit(main())
