#!/usr/bin/env python
"""Out-of-panel validation for an FS accuracy lever.

Every FS threshold/weight lever in the current campaign (Otsu calibration #2078,
evidence-cut #2095, post-blocking-u #2091) was measured ONLY on the tuning panel
(historical_50k / dblp_acm / febrl3 / synthetic_person). That panel is the
lever's *training set* — its selector or knob was fit against exactly those four
shapes — so an in-panel F1 delta cannot tell us whether the lever generalises.

This runs the GoldenMatch probabilistic path with a lever's env var OFF vs ON
across two dataset groups:
  * CONTROL  — in-panel sets where the lever reported a win (reproduce it here)
  * HOLDOUT  — datasets the lever NEVER saw (febrl4, dblp_scholar, amazon_google)

If the win reproduces in CONTROL but goes flat/negative in HOLDOUT, the lever is
panel-overfit (the honest "no"). If it holds out-of-panel, that's real signal.

Usage:
  python validate_fs_holdout.py --lever-env GOLDENMATCH_FS_CALIBRATE_THRESHOLD \
      --on 1 --control dblp_acm,febrl3 --holdout febrl4,dblp_scholar,amazon_google \
      --out .profile_tmp/fs_holdout
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def _import_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_one(name, mods, out_dir, threshold):
    """Load a dataset, run the GM probabilistic path once, return the panel row.
    The lever env var is read INSIDE dedupe, so the caller sets it before this."""
    datasets_mod, _attr, _eval = mods
    run_panel = _import_sibling("run_panel")
    records, truth = datasets_mod.load_dataset(name)
    ds_dir = out_dir / name
    ds_dir.mkdir(parents=True, exist_ok=True)
    truth_path = ds_dir / "truth.parquet"
    pq.write_table(truth, truth_path)
    return run_panel._run_goldenmatch(
        name, records, truth, out_dir, truth_path, threshold, mods
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lever-env", required=True, help="env var to toggle")
    ap.add_argument("--on", default="1", help="value for the ON state")
    ap.add_argument("--control", default="dblp_acm,febrl3")
    ap.add_argument("--holdout", default="febrl4,dblp_scholar,amazon_google")
    ap.add_argument("--out", type=Path, default=Path(".profile_tmp/fs_holdout"))
    args = ap.parse_args()

    # Cross-run autoconfig memory would leak state between the OFF and ON runs.
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"

    datasets_mod = _import_sibling("datasets")
    attribution_mod = _import_sibling("attribution")
    evaluate_mod = _import_sibling("evaluate")
    mods = (datasets_mod, attribution_mod, evaluate_mod)

    control = [d.strip() for d in args.control.split(",") if d.strip()]
    holdout = [d.strip() for d in args.holdout.split(",") if d.strip()]
    groups = [("control", control), ("holdout", holdout)]

    rows: list[dict] = []
    for group, names in groups:
        for name in names:
            rec: dict = {"group": group, "dataset": name, "lever": args.lever_env}
            for state, val in (("off", None), ("on", args.on)):
                if val is None:
                    os.environ.pop(args.lever_env, None)
                else:
                    os.environ[args.lever_env] = val
                print(f"\n=== {group}/{name} [{args.lever_env}={val or 'unset'}] ===",
                      flush=True)
                r = _run_one(name, mods, args.out / state, threshold=0.85)
                rec[f"{state}_status"] = r.get("status")
                for k in ("precision", "recall", "f1"):
                    rec[f"{state}_{k}"] = r.get(k)
                if r.get("status") != "ok":
                    rec[f"{state}_error"] = r.get("error") or r.get("reason")
            if rec.get("on_f1") is not None and rec.get("off_f1") is not None:
                rec["delta_f1"] = round(rec["on_f1"] - rec["off_f1"], 4)
            rows.append(rec)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "holdout_result.json").write_text(json.dumps(rows, indent=2))

    # Markdown table
    print("\n\n## FS lever out-of-panel validation:", args.lever_env)
    print("\n| group | dataset | F1 OFF | F1 ON | ΔF1 | P OFF→ON | R OFF→ON |")
    print("|---|---|---|---|---|---|---|")
    def _f(v):
        return f"{v:.4f}" if isinstance(v, (int, float)) else "-"
    for r in rows:
        d = r.get("delta_f1")
        dstr = f"**{d:+.4f}**" if isinstance(d, float) else "-"
        print(
            f"| {r['group']} | {r['dataset']} | {_f(r.get('off_f1'))} | "
            f"{_f(r.get('on_f1'))} | {dstr} | "
            f"{_f(r.get('off_precision'))}→{_f(r.get('on_precision'))} | "
            f"{_f(r.get('off_recall'))}→{_f(r.get('on_recall'))} |"
        )
        for st in ("off", "on"):
            if r.get(f"{st}_status") != "ok":
                print(f"|  | ⚠ {st}: {r.get(f'{st}_error')} |||||||")


if __name__ == "__main__":
    main()
