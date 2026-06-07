"""Fellegi-Sunter score-calibration sweep: linear vs posterior x threshold.

Settles the `_FS_CALIBRATION_DEFAULT` decision with data instead of a comment.
The two calibrations (probabilistic.py):

  - linear    : (W - W_min) / (W_max - W_min). Monotonic in the summed match
                weight W but NOT a probability; its default link cut is 0.50 of
                the achievable weight range.
  - posterior : 1 / (1 + 2^-(prior_w + W)) — the true FS match probability. Its
                "natural" cut is 0.50 (Bayes boundary), but that cut is only
                well-calibrated if the EM prior is; this sweep varies the cut.

For each dataset it prints linear (at its built-in thresholds) and posterior
across a threshold grid, with P/R/F1 and pair counts, so the default can be
chosen by max F1 with eyes open.

Only datasets present under tests/benchmarks/datasets are run (Febrl etc. are
skipped with a note rather than faked).

Usage:
    uv run python packages/python/goldenmatch/scripts/bench_fs_calibration.py \
        [--thresholds 0.5,0.9,0.99,0.999,0.9999]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import polars as pl

DATASETS = Path(__file__).parent.parent / "tests" / "benchmarks" / "datasets"


def _eval(pairs, df, gt, left_src, right_src, left_id_col, right_id_col):
    rows = df.to_dicts()
    idx = {r["__row_id__"]: i for i, r in enumerate(rows)}
    tp = fp = 0
    for a, b, _s in pairs:
        ia, ib = idx.get(a), idx.get(b)
        if ia is None or ib is None:
            continue
        ra, rb = rows[ia], rows[ib]
        if ra.get("__source__") == rb.get("__source__"):
            continue
        if ra.get("__source__") == left_src:
            pair = (str(ra.get("id", "")).strip(), str(rb.get("id", "")).strip())
        else:
            pair = (str(rb.get("id", "")).strip(), str(ra.get("id", "")).strip())
        if pair in gt:
            tp += 1
        else:
            fp += 1
    fn = len(gt) - tp
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1, len(pairs)


def _load_dblp_acm():
    d = DATASETS / "DBLP-ACM"
    if not d.exists():
        return None
    a = pl.read_csv(d / "DBLP2.csv", encoding="utf8-lossy", infer_schema_length=10000,
                    ignore_errors=True).cast(pl.Utf8).with_columns(pl.lit("dblp").alias("__source__"))
    b = pl.read_csv(d / "ACM.csv", encoding="utf8-lossy", infer_schema_length=10000,
                    ignore_errors=True).cast(pl.Utf8).with_columns(pl.lit("acm").alias("__source__"))
    df = pl.concat([a, b], how="diagonal").with_row_index("__row_id__")
    gt = {(str(r["idDBLP"]).strip(), str(r["idACM"]).strip())
          for r in pl.read_csv(d / "DBLP-ACM_perfectMapping.csv").to_dicts()}
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(name="fs", type="probabilistic", fields=[
        MatchkeyField(field="title", scorer="token_sort", levels=3, partial_threshold=0.8, transforms=["lowercase"]),
        MatchkeyField(field="authors", scorer="token_sort", levels=3, partial_threshold=0.7, transforms=["lowercase"]),
        MatchkeyField(field="year", scorer="exact", levels=2),
    ])
    return {"name": "DBLP-ACM", "df": df, "gt": gt, "mk": mk,
            "block_field": "year", "left_src": "dblp"}


def _score(df, mk, blocking_field, link_threshold=None):
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.probabilistic import probabilistic_block_scorer, train_em
    blocking = BlockingConfig(keys=[BlockingKeyConfig(fields=[blocking_field])], max_block_size=1000)
    blocks = build_blocks(df.lazy(), blocking)
    em = train_em(df, mk, n_sample_pairs=15000, max_iterations=25, blocks=blocks,
                  blocking_fields=[blocking_field])
    if link_threshold is not None:
        mk = mk.model_copy(update={"link_threshold": link_threshold})
    scorer = probabilistic_block_scorer(mk, em)
    pairs = []
    for blk in blocks:
        bdf = blk.df.collect() if hasattr(blk.df, "collect") else blk.df
        pairs.extend(scorer(bdf))
    return pairs, em


def run(thresholds: list[float]) -> None:
    ds = _load_dblp_acm()
    available = [d for d in (ds,) if d is not None]
    if not available:
        print("No benchmark datasets present under tests/benchmarks/datasets — nothing to sweep.")
        return
    for d in available:
        print(f"\n=== {d['name']} ===", flush=True)
        # linear at built-in thresholds
        os.environ["GOLDENMATCH_FS_CALIBRATED"] = "linear"
        pairs, _ = _score(d["df"], d["mk"], d["block_field"])
        p, r, f1, n = _eval(pairs, d["df"], d["gt"], d["left_src"], None, None, None)
        print(f"  {'linear (default cut)':<26s}  P={p:.3f} R={r:.3f} F1={f1:.3f}  ({n} pairs)")
        # posterior across the threshold grid
        os.environ["GOLDENMATCH_FS_CALIBRATED"] = "posterior"
        best = None
        for t in thresholds:
            pairs, _ = _score(d["df"], d["mk"], d["block_field"], link_threshold=t)
            p, r, f1, n = _eval(pairs, d["df"], d["gt"], d["left_src"], None, None, None)
            print(f"  {'posterior @ ' + format(t, '.4g'):<26s}  P={p:.3f} R={r:.3f} F1={f1:.3f}  ({n} pairs)")
            if best is None or f1 > best[1]:
                best = (t, f1)
        print(f"  -> best posterior cut: {best[0]:.4g} (F1={best[1]:.3f})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", default="0.5,0.9,0.99,0.999,0.9999")
    args = ap.parse_args()
    ts = [float(x) for x in args.thresholds.split(",") if x.strip()]
    run(ts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
