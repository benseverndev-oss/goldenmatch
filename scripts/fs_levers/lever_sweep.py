#!/usr/bin/env python3
"""Do the untuned FS levers matter, and can the healer reach them?

Two sweeps over the same baseline config, per dataset:

  A. AUTOCONFIG levers -- `levels` and `partial_threshold` are set to fixed
     constants by `build_probabilistic_matchkeys` (levels=3, partial=0.6/0.8)
     and never revisited by anything. Sweep them and see whether F1 moves.

  B. HEALER/OPTIMIZER levers -- the closed ConfigEdit vocabulary. Only
     `threshold_shift` reaches anything FS-specific; the rest are shared with
     the weighted path. Sweep what it CAN reach.

The comparison that matters is ceiling(A) vs ceiling(B). If A's ceiling is
meaningfully above B's, the healer cannot get there from here no matter how
long it searches, because the lever is not in its vocabulary.

Everything is measured against the same evaluator on the same ground truth,
so the numbers are comparable across both sweeps.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import polars as pl  # noqa: E402


def _f1(result, gt_pairs) -> dict:
    from goldenmatch.core.evaluate import evaluate_clusters
    ev = evaluate_clusters(result.clusters, gt_pairs).summary()
    return {"f1": ev["f1"], "precision": ev["precision"], "recall": ev["recall"]}


def _run(df, cfg, gt_pairs) -> dict:
    import goldenmatch
    t = time.perf_counter()
    res = goldenmatch.dedupe_df(df, config=cfg)
    out = _f1(res, gt_pairs)
    out["seconds"] = round(time.perf_counter() - t, 2)
    return out


def _with_levels(cfg, levels: int | None, partial: float | None):
    """Copy cfg, overriding levels / partial_threshold on every FS field."""
    new = cfg.model_copy(deep=True)
    for mk in new.get_matchkeys():
        if getattr(mk, "type", None) != "probabilistic":
            continue
        for f in (mk.fields or []):
            if levels is not None:
                f.levels = levels
            if partial is not None:
                f.partial_threshold = partial
    return new


def sweep_autoconfig(df, cfg, gt, levels_grid, partial_grid) -> list[dict]:
    """Sweep the two levers autoconfig hardcodes and never tunes."""
    rows = []
    for lv in levels_grid:
        for pt in partial_grid:
            try:
                r = _run(df, _with_levels(cfg, lv, pt), gt)
            except Exception as e:  # noqa: BLE001 - one cell must not lose the grid
                r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
            r.update({"lever": "autoconfig", "levels": lv, "partial_threshold": pt})
            rows.append(r)
            print(f"    levels={lv} partial={pt}: "
                  f"{r.get('f1', r.get('error'))}", flush=True)
    return rows


def sweep_healer(df, cfg, gt, offsets, scorers) -> list[dict]:
    """Sweep what the closed ConfigEdit vocabulary can actually reach.

    Mirrors config_optimizer's own edit families: ThresholdShift over the
    offset grid, ScorerSwap over the scorer grid. These are the FS-reachable
    members; weight_shift is weighted-only, and blocking edits are held fixed
    so the comparison isolates the matchkey.
    """
    from goldenmatch.core.config_edits import ScorerSwap, ThresholdShift
    rows = []
    for o in offsets:
        edit = ThresholdShift(o)
        new = edit.apply(cfg)
        if new is None:
            print(f"    {edit.label}: NOT APPLICABLE", flush=True)
            rows.append({"lever": "healer", "edit": edit.label,
                         "applicable": False})
            continue
        try:
            r = _run(df, new, gt)
        except Exception as e:  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        r.update({"lever": "healer", "edit": edit.label, "applicable": True})
        rows.append(r)
        print(f"    {edit.label}: {r.get('f1', r.get('error'))}", flush=True)

    for mk in cfg.get_matchkeys():
        if getattr(mk, "type", None) != "probabilistic":
            continue
        for f in (mk.fields or []):
            for sc in scorers:
                if f.scorer == sc:
                    continue
                edit = ScorerSwap(mk.name, f.field, sc)
                new = edit.apply(cfg)
                if new is None:
                    continue
                try:
                    r = _run(df, new, gt)
                except Exception as e:  # noqa: BLE001
                    r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
                r.update({"lever": "healer", "edit": edit.label,
                          "applicable": True})
                rows.append(r)
                print(f"    {edit.label}: {r.get('f1', r.get('error'))}",
                      flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="+",
                    default=["synthetic", "anchor_person_match"])
    ap.add_argument("--row-cap", type=int, default=None)
    ap.add_argument("--levels", nargs="+", type=int, default=[2, 3, 5])
    ap.add_argument("--partials", nargs="+", type=float,
                    default=[0.6, 0.8, 0.9])
    ap.add_argument("--offsets", nargs="+", type=float,
                    default=[-0.10, -0.05, 0.05, 0.10])
    ap.add_argument("--scorers", nargs="+",
                    default=["jaro_winkler", "token_sort", "levenshtein"])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from scripts.suggest_quality.datasets import REGISTRY

    by_name = {d.name: d for d in REGISTRY}
    report = []
    for name in args.datasets:
        ds = by_name.get(name)
        if ds is None:
            print(f"[skip] unknown dataset {name}")
            continue
        loaded = ds.loader()
        if loaded is None:
            print(f"[skip] {name}: data not present locally")
            continue
        df, gt = loaded
        if args.row_cap and df.height > args.row_cap:
            df = df.head(args.row_cap)
            gt = {(a, b) for a, b in gt if a < args.row_cap and b < args.row_cap}
        print(f"\n=== {name}: rows={df.height} gt_pairs={len(gt)}", flush=True)

        cfg = auto_configure_probabilistic_df(df)
        base = _run(df, cfg, gt)
        # What autoconfig actually chose, so the baseline is inspectable.
        chosen = [
            {"field": f.field, "scorer": f.scorer, "levels": f.levels,
             "partial_threshold": f.partial_threshold}
            for mk in cfg.get_matchkeys() if getattr(mk, "type", None) == "probabilistic"
            for f in (mk.fields or [])
        ]
        print(f"  baseline f1={base['f1']:.4f}  fields={len(chosen)}", flush=True)
        print("  AUTOCONFIG sweep (levels x partial_threshold):", flush=True)
        a = sweep_autoconfig(df, cfg, gt, args.levels, args.partials)
        print("  HEALER sweep (closed ConfigEdit vocabulary):", flush=True)
        h = sweep_healer(df, cfg, gt, args.offsets, args.scorers)

        report.append({
            "dataset": name, "rows": df.height, "gt_pairs": len(gt),
            "baseline": base, "autoconfig_chose": chosen,
            "autoconfig_sweep": a, "healer_sweep": h,
        })
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
