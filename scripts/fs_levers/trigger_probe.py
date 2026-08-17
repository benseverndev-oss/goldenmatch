#!/usr/bin/env python3
"""Do the FS runtime warnings PREDICT which lever to pull?

Capability without a trigger is not usable. This asks whether the two signals
the engine already emits are discriminative:

  monotonicity  "FS match weights are non-monotonic for field(s): X"
                fires when a partial agreement level outweighs exact agreement,
                i.e. the level structure does not fit the data. Candidate
                trigger for a `levels` change.

  fallback cut  "linked N% of records using a FALLBACK link cutoff of 0.5000"
                fires when no link_threshold was set AND EM produced no
                calibrated cutoff. Candidate trigger for setting link_threshold.

For each (dataset, levels) cell it records which warnings fired, how many
fields they named, and the resulting F1. A signal is a usable trigger only if
its firing tracks whether that cell is BETTER or WORSE than the shipped
default -- a warning that fires everywhere carries no information.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import logging
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_MONO = re.compile(r"non-monotonic for field\(s\): ([^—]+?) —")
_FALLBACK = re.compile(r"linked ([\d.]+)% .*FALLBACK link cutoff of ([\d.]+)")
_NOCONV = re.compile(r"EM did not converge after (\d+) iterations \(delta=([\d.]+)\)")


class _Capture(logging.Handler):
    """These messages go through `logging`, NOT `warnings.warn`.

    The first version of this probe wrapped the run in
    `warnings.catch_warnings(record=True)` and recorded nothing -- every
    `*_fired` came back False while the warnings printed to the console.
    A capture that silently records nothing looks exactly like a clean run,
    which is the same false-negative shape this session already hit twice.
    """

    def __init__(self):
        super().__init__(level=0)
        self.messages: list[str] = []

    def emit(self, record):
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass


def _with_levels(cfg, levels):
    new = cfg.model_copy(deep=True)
    for mk in new.get_matchkeys():
        if getattr(mk, "type", None) != "probabilistic":
            continue
        for f in (mk.fields or []):
            f.levels = levels
    return new


def _run_capturing(df, cfg, gt):
    import goldenmatch
    from goldenmatch.core.evaluate import evaluate_clusters
    cap = _Capture()
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            t = time.perf_counter()
            res = goldenmatch.dedupe_df(df, config=cfg)
            secs = round(time.perf_counter() - t, 2)
    finally:
        root.removeHandler(cap)
        root.setLevel(prev_level)
    # Both channels: the engine uses logging for these, but capture
    # warnings.warn too so a future move between channels cannot go dark.
    msgs = [str(w.message) for w in caught] + cap.messages
    if not msgs:
        raise RuntimeError("capture recorded NOTHING -- instrument is broken")
    mono_fields, fallback, noconv = [], None, None
    for m in msgs:
        hit = _MONO.search(m)
        if hit:
            mono_fields += [s.strip() for s in hit.group(1).split(",") if s.strip()]
        hit = _FALLBACK.search(m)
        if hit:
            fallback = {"match_rate_pct": float(hit.group(1)),
                        "cutoff": float(hit.group(2))}
        hit = _NOCONV.search(m)
        if hit:
            noconv = {"iterations": int(hit.group(1)),
                      "delta": float(hit.group(2))}
    ev = evaluate_clusters(res.clusters, gt).summary()
    return {
        "f1": ev["f1"], "precision": ev["precision"], "recall": ev["recall"],
        "seconds": secs,
        "monotonicity_fired": bool(mono_fields),
        "monotonic_bad_fields": sorted(set(mono_fields)),
        "n_monotonic_bad": len(set(mono_fields)),
        "fallback_cutoff_fired": fallback is not None,
        "fallback": fallback,
        "em_nonconvergence_fired": noconv is not None,
        "em_nonconvergence": noconv,
        "n_messages_captured": len(msgs),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="+", required=True)
    ap.add_argument("--levels", nargs="+", type=int, default=[2, 3, 5])
    ap.add_argument("--row-cap", type=int, default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from scripts.suggest_quality.datasets import REGISTRY
    by_name = {d.name: d for d in REGISTRY}

    report = []
    for name in args.datasets:
        ds = by_name.get(name)
        loaded = ds.loader() if ds else None
        if loaded is None:
            print(f"[skip] {name}")
            continue
        df, gt = loaded
        if args.row_cap and df.height > args.row_cap:
            df = df.head(args.row_cap)
            gt = {(a, b) for a, b in gt if a < args.row_cap and b < args.row_cap}
        cfg = auto_configure_probabilistic_df(df)
        print(f"\n=== {name} rows={df.height}", flush=True)

        base = _run_capturing(df, cfg, gt)
        print(f"  baseline(as-shipped) f1={base['f1']:.4f} "
              f"mono={base['monotonicity_fired']}{base['monotonic_bad_fields']} "
              f"fallback={base['fallback_cutoff_fired']}", flush=True)

        cells = []
        for lv in args.levels:
            r = _run_capturing(df, _with_levels(cfg, lv), gt)
            r["levels"] = lv
            r["delta_vs_baseline"] = round(r["f1"] - base["f1"], 4)
            cells.append(r)
            print(f"  levels={lv}: f1={r['f1']:.4f} "
                  f"(delta {r['delta_vs_baseline']:+.4f})  "
                  f"mono_fired={r['monotonicity_fired']} "
                  f"n_bad={r['n_monotonic_bad']} "
                  f"fallback={r['fallback_cutoff_fired']}"
                  f"{r['fallback']['match_rate_pct'] if r['fallback'] else ''} "
                  f"EM_noconv={r['em_nonconvergence_fired']}", flush=True)

        report.append({"dataset": name, "rows": df.height,
                       "baseline": base, "cells": cells})
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
