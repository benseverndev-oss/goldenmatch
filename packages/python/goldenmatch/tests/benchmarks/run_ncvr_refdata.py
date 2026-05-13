"""NCVR — surname-frequency-weighted scorer A/B benchmark.

Runs the existing NCVR 10K corrupted-duplicates GT (the same shape used by
``test_autoconfig_ncvr_meets_target``) under two configs:

  A. **baseline** — zero-config auto-config (matches existing public number
     of F1=0.9719 on this fixture).
  B. **refdata** — same auto-config result, then last_name's scorer is
     rewritten to ``name_freq_weighted_jw`` before running.

Reports precision, recall, F1 for each, plus the F1 delta. Standalone
runner; the dataset is gitignored, so this is not in the default CI test
set.

Usage::

    python tests/benchmarks/run_ncvr_refdata.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Any

import polars as pl

import copy

import goldenmatch
import goldenmatch.refdata  # noqa: F401  registers name_freq_weighted_jw

DATASETS = Path(__file__).parent / "datasets"
NCVR_SAMPLE = DATASETS / "NCVR" / "ncvoter_sample_10k.txt"

SEED = 42
N_BASE = 5_000
N_DUPES = N_BASE // 2
CORRUPT_FIELDS = ["first_name", "last_name", "middle_name",
                  "res_street_address", "zip_code"]


def _load_and_corrupt() -> tuple[pl.DataFrame, set[tuple]]:
    df = pl.read_csv(NCVR_SAMPLE, separator="\t",
                     encoding="utf8-lossy", ignore_errors=True)
    df = df.filter(
        (pl.col("last_name").str.len_chars() > 1) &
        (pl.col("first_name").str.len_chars() > 1)
    )
    df = df.sample(n=min(N_BASE, df.height), seed=SEED)
    keep_cols = ["ncid", "first_name", "last_name", "middle_name",
                 "res_street_address", "res_city_desc", "state_cd",
                 "zip_code", "birth_year", "gender_code"]
    df = df.select([c for c in keep_cols if c in df.columns])

    rng = random.Random(SEED)
    rows = df.to_dicts()
    dup_indices = rng.sample(range(len(rows)), min(N_DUPES, len(rows)))

    def _corrupt(val: str | None) -> str | None:
        if val is None or len(val) < 2:
            return val
        op = rng.choice(["typo", "swap", "drop", "abbreviate", "case"])
        if op == "typo":
            pos = rng.randint(0, len(val) - 1)
            return val[:pos] + rng.choice("abcdefghijklmnopqrstuvwxyz") + val[pos + 1:]
        if op == "swap" and len(val) >= 3:
            pos = rng.randint(0, len(val) - 2)
            return val[:pos] + val[pos + 1] + val[pos] + val[pos + 2:]
        if op == "drop" and len(val) >= 3:
            pos = rng.randint(0, len(val) - 1)
            return val[:pos] + val[pos + 1:]
        if op == "abbreviate" and len(val) >= 3:
            return val[0] + "."
        if op == "case":
            return val.lower() if rng.random() < 0.5 else val.upper()
        return val

    corrupted = []
    gt: set[tuple] = set()
    for orig_idx in dup_indices:
        original = rows[orig_idx]
        dup = dict(original)
        dup["ncid"] = original["ncid"] + "_DUP"
        for field in CORRUPT_FIELDS:
            if rng.random() < 0.30:
                dup[field] = _corrupt(dup.get(field))
        corrupted.append(dup)
        a, b = original["ncid"], dup["ncid"]
        gt.add((min(a, b), max(a, b)))

    combined = pl.DataFrame(rows + corrupted)
    return combined, gt


def _rewrite_last_name_scorer(config: Any, scorer: str) -> Any:
    """Deep-copy ``config`` and rewrite every fuzzy/weighted matchkey field
    whose column is ``last_name`` to use ``scorer``. Returns the new config."""
    cfg = copy.deepcopy(config)
    rewritten = 0
    for mk in cfg.get_matchkeys():
        if mk.type not in ("weighted", "probabilistic"):
            continue
        for f in mk.fields:
            if f.field == "last_name":
                f.scorer = scorer
                rewritten += 1
    if rewritten == 0:
        # Auto-config picked a different field shape; the A/B isn't testing
        # what we think it is. Surface this loudly.
        print(f"warning: no last_name fields found to rewrite to {scorer}")
    return cfg


def _score(result: Any, ncid_lookup: list[str], gt: set[tuple]) -> dict:
    found: set[tuple] = set()
    for c in result.clusters.values():
        members = sorted(c["members"])
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = ncid_lookup[members[i]], ncid_lookup[members[j]]
                found.add((min(a, b), max(a, b)))
    tp = len(found & gt)
    fp = len(found - gt)
    fn = len(gt - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1,
            "found": len(found), "gt": len(gt)}


def main() -> int:
    if not NCVR_SAMPLE.exists():
        print(f"NCVR sample missing at {NCVR_SAMPLE}", file=sys.stderr)
        return 2

    combined, gt = _load_and_corrupt()
    ncid_lookup = combined["ncid"].to_list()
    print(f"Loaded {combined.height:,} records ({len(gt):,} GT pairs)")
    print()

    # Seed: zero-config auto-config produces the baseline matchkey shape.
    # We then derive the refdata variant from it by swapping last_name's scorer.
    from goldenmatch.core.autoconfig import auto_configure_df

    base_cfg = auto_configure_df(combined)
    refdata_cfg = _rewrite_last_name_scorer(base_cfg, "name_freq_weighted_jw")

    configs = [("baseline (zero-config)", base_cfg),
               ("refdata  (last_name -> name_freq_weighted_jw)", refdata_cfg)]
    metrics = []
    for label, cfg in configs:
        result = goldenmatch.dedupe_df(combined, config=cfg)
        m = _score(result, ncid_lookup, gt)
        metrics.append((label, m))
        print(f"{label}")
        print(f"  precision={m['precision']:.4f}  recall={m['recall']:.4f}  "
              f"f1={m['f1']:.4f}  (tp={m['tp']}, fp={m['fp']}, fn={m['fn']})")
        print()

    base_f1 = metrics[0][1]["f1"]
    ref_f1 = metrics[1][1]["f1"]
    base_p = metrics[0][1]["precision"]
    ref_p = metrics[1][1]["precision"]
    base_r = metrics[0][1]["recall"]
    ref_r = metrics[1][1]["recall"]
    print(f"F1 delta:  {ref_f1 - base_f1:+.4f}  ({base_f1:.4f} -> {ref_f1:.4f})")
    print(f"P delta:   {ref_p - base_p:+.4f}  ({base_p:.4f} -> {ref_p:.4f})")
    print(f"R delta:   {ref_r - base_r:+.4f}  ({base_r:.4f} -> {ref_r:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
