#!/usr/bin/env python3
"""Dump the score distribution the FS calibrator actually sees, per dataset.

## Why this exists

Two attempts to fix the per-dataset link-threshold calibrator were each
validated against a SYNTHETIC fixture built to resemble a failing dataset, and
each failed on the real one:

    historical_50k   -0.7457  ->  -0.7457   (unchanged)
    dblp_scholar     -0.0381  ->  -0.1232   (three times worse)

Both fixtures were wrong. I was modelling my mental picture of the problem
rather than the problem. This measures the real thing instead of guessing at it
a third time.

## What it answers

For each dataset, the calibrator sees one array of training-pair scores and has
to place a cut. The question it cannot answer for itself -- and that no
synthetic fixture can settle -- is whether the two classes are SEPARABLE at all:

* **Separable**: matches and non-matches occupy distinct regions with a trough
  between them. A cut exists; finding it is a solvable problem.
* **Overlapping**: one continuous mass, genuine matches scoring below genuine
  non-matches. NO cut is correct -- only a choice about which error to prefer --
  and any calibrator that reports `source: "calibrated"` here is asserting a
  boundary that does not exist.

So this dumps the histogram TWICE: once as the calibrator sees it (unlabelled),
and once split by ground truth. The labelled split is the whole point. An
unlabelled histogram cannot distinguish "one mode" from "two modes that
overlap", and that distinction is the finding.

It also reports, per candidate cut, the F1 that cut would achieve on the
TRAINING pairs -- so the best achievable cut is visible next to the one the
calibrator chose.

Usage:
    python scripts/bench_er_headtohead/dump_fs_score_histograms.py \\
        --datasets historical_50k dblp_scholar --out fs-histograms.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

BINS = 40
_BASIC = {"jaro_winkler", "levenshtein", "token_sort", "exact"}


def truth_pairs(truth) -> set[tuple[str, str]]:
    """Canonical (min, max) record-id pairs that share a true cluster."""
    import itertools
    from collections import defaultdict

    by_cluster: dict = defaultdict(list)
    rid = [str(x) for x in truth.column("record_id").to_pylist()]
    cid = truth.column("cluster_id").to_pylist()
    for r, c in zip(rid, cid):
        by_cluster[c].append(r)
    out: set = set()
    for members in by_cluster.values():
        if len(members) > 1:
            for a, b in itertools.combinations(sorted(members), 2):
                out.add((a, b))
    return out


def collect(name: str, *, basic_scorers: bool = True) -> dict:
    """Score every blocked training pair, and label it against ground truth.

    ``basic_scorers`` rewrites the specialised name scorers down to plain
    ``jaro_winkler``, which is right for a Splink-comparable measurement and
    WRONG as a statement of what GoldenMatch does -- on the head-to-head panel
    that rewrite alone costs the person shape 0.078 F1, all of it recall. Pass
    ``False`` to probe the distribution the shipped configuration actually
    produces. Recorded in the output either way so a reader never has to infer
    which one they are looking at.
    """
    import datasets as datasets_mod
    import numpy as np
    from goldenmatch.core import probabilistic as P
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    records, truth = datasets_mod.load_dataset(name)
    tp = truth_pairs(truth)

    cfg = auto_configure_probabilistic_df(records)
    mk = cfg.get_matchkeys()[0]
    rewritten = []
    if basic_scorers:
        for f in getattr(mk, "fields", None) or []:
            if f.scorer and f.scorer not in _BASIC:
                rewritten.append(f"{f.field}:{f.scorer}->jaro_winkler")
                f.scorer = "jaro_winkler"

    # Intercept the exact array the calibrator is handed. Reaching in like this
    # is deliberate: re-deriving the scores here would measure a lookalike, and
    # a lookalike is what produced two wrong fixes already.
    captured: dict = {}
    orig = P._posterior_split

    def spy(scores):
        captured["scores"] = np.asarray(scores, dtype=np.float64).copy()
        return orig(scores)

    P._posterior_split = spy
    try:
        from goldenmatch import dedupe_df

        res = dedupe_df(records, config=cfg)
    finally:
        P._posterior_split = orig

    out: dict = {"dataset": name, "n_records": records.num_rows,
                 "n_true_pairs": len(tp),
                 "basic_scorers": bool(basic_scorers),
                 "scorers_rewritten": rewritten,
                 "comparable_to_splink": bool(basic_scorers)}
    stats = (getattr(res, "stats", None) or {}).get("fs_link_thresholds") or {}
    first = next(iter(stats.values()), {}) if isinstance(stats, dict) else {}
    out["chosen_threshold"] = first.get("link_threshold")
    out["threshold_source"] = first.get("source")

    scores = captured.get("scores")
    if scores is None:
        out["error"] = "the calibrator never ran (no scores captured)"
        return out

    hist, edges = np.histogram(scores, bins=BINS, range=(0.0, 1.0))
    out["n_training_pairs"] = int(scores.shape[0])
    out["hist_unlabelled"] = hist.astype(int).tolist()
    out["bin_edges"] = [round(float(e), 4) for e in edges]
    out["percentiles"] = {
        str(q): round(float(np.percentile(scores, q)), 6)
        for q in (1, 5, 25, 50, 75, 95, 99)
    }

    # The labelled split needs the pair ids alongside the scores, which the spy
    # cannot see -- so it is rebuilt from the SAME sampler + comparison matrix
    # the trainer used, rather than from a fresh sample that would not line up.
    labelled = _labelled_scores(records, mk, cfg, tp)
    if labelled is not None:
        m_hist, _ = np.histogram(labelled["match"], bins=BINS, range=(0.0, 1.0))
        n_hist, _ = np.histogram(labelled["nonmatch"], bins=BINS, range=(0.0, 1.0))
        out["hist_match"] = m_hist.astype(int).tolist()
        out["hist_nonmatch"] = n_hist.astype(int).tolist()
        out["n_labelled_match"] = int(labelled["match"].shape[0])
        out["n_labelled_nonmatch"] = int(labelled["nonmatch"].shape[0])
        out["overlap"] = _overlap_report(labelled["match"], labelled["nonmatch"])
        out["cut_curve"] = _cut_curve(labelled["match"], labelled["nonmatch"])
    return out


def _labelled_scores(records, mk, cfg, tp) -> dict | None:
    """Training-pair posteriors, split by whether the pair is a true match."""
    import numpy as np
    import polars as pl
    from goldenmatch.core import probabilistic as P
    from goldenmatch.core.blocker import build_blocks

    # `datasets.load_dataset` returns Arrow; the trainer wants polars, and
    # `to_frame(...).native` hands the Arrow table straight back rather than
    # converting. Convert explicitly.
    df = pl.from_arrow(records)
    if "__row_id__" not in df.columns:
        df = df.with_columns(pl.arange(0, df.height).alias("__row_id__"))
    rid = [str(x) for x in df["record_id"].to_list()]

    blocks = build_blocks(df, cfg.blocking)
    pairs, _cond = P._sample_blocked_pairs_with_fields(blocks, 20000, 42)
    if len(pairs) < 50:
        return None

    cols = P._fs_projection_cols(mk)
    lookup = P._row_lookup_for_pairs(df, cols, [pairs])
    comp = P._build_comparison_matrix(pairs, lookup, mk)

    em = P.train_em(df, mk, blocks=blocks)
    weights = {f.field: np.asarray(em.match_weights[f.field], dtype=np.float64)
               for f in mk.fields if f.field in em.match_weights}
    total = np.zeros(comp.shape[0], dtype=np.float64)
    for j, f in enumerate(mk.fields):
        if f.field not in weights:
            continue
        lv = comp[:, j]
        obs = lv >= 0
        total[obs] += weights[f.field][lv[obs]]

    prior_w = P.prior_weight(em.proportion_matched)
    post = np.asarray([P.posterior_from_weight(float(w), prior_w) for w in total])

    is_match = np.asarray([
        (min(rid[a], rid[b]), max(rid[a], rid[b])) in tp for a, b in pairs
    ])
    return {"match": post[is_match], "nonmatch": post[~is_match]}


def _overlap_report(match, nonmatch) -> dict:
    """How badly the two classes interpenetrate.

    `separable` is the question the whole exercise turns on: if the lowest true
    match scores below the highest true non-match, no cut can be clean and the
    only choice is which error to accept.
    """
    import numpy as np

    if match.size == 0 or nonmatch.size == 0:
        return {"separable": None, "note": "one class is empty"}
    m_lo, n_hi = float(match.min()), float(nonmatch.max())
    return {
        "match_min": round(m_lo, 6),
        "match_p05": round(float(np.percentile(match, 5)), 6),
        "match_median": round(float(np.median(match)), 6),
        "nonmatch_median": round(float(np.median(nonmatch)), 6),
        "nonmatch_p95": round(float(np.percentile(nonmatch, 95)), 6),
        "nonmatch_max": round(n_hi, 6),
        "separable": bool(m_lo > n_hi),
        "matches_below_nonmatch_p95": int(
            (match < np.percentile(nonmatch, 95)).sum()
        ),
    }


def _cut_curve(match, nonmatch) -> list[dict]:
    """F1 on the TRAINING pairs at each candidate cut.

    Shows the best achievable cut beside whatever the calibrator picked. A flat
    curve means the cut barely matters; a curve still climbing at 0.99 means the
    optimum is outside the range anyone has searched.
    """

    out = []
    for t in [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 0.999]:
        tp_ = int((match >= t).sum())
        fp_ = int((nonmatch >= t).sum())
        fn_ = int((match < t).sum())
        p = tp_ / max(tp_ + fp_, 1)
        r = tp_ / max(tp_ + fn_, 1)
        out.append({
            "cut": t, "precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / max(p + r, 1e-9), 4),
        })
    return out


def render(reports: list[dict]) -> str:
    out = ["## FS score distributions (what the calibrator sees)", ""]
    for r in reports:
        out.append(f"### {r['dataset']}")
        out.append("")
        if r.get("error"):
            out += [f"**{r['error']}**", ""]
            continue
        out.append(
            f"{r.get('n_training_pairs', 0):,} training pairs · calibrator chose "
            f"**{r.get('chosen_threshold')}** (`{r.get('threshold_source')}`)"
        )
        ov = r.get("overlap") or {}
        if ov:
            sep = ov.get("separable")
            out += ["", f"**Separable: {sep}**  ·  lowest true match "
                        f"`{ov.get('match_min')}` vs highest true non-match "
                        f"`{ov.get('nonmatch_max')}`", ""]
        if r.get("cut_curve"):
            out += ["| cut | P | R | F1 |", "|---|---|---|---|"]
            for c in r["cut_curve"]:
                out.append(f"| {c['cut']} | {c['precision']} | {c['recall']} "
                           f"| {c['f1']} |")
            out.append("")
        if r.get("hist_match"):
            out += ["| bin | non-match | match |", "|---|---|---|"]
            edges = r["bin_edges"]
            for i, (n, m) in enumerate(zip(r["hist_nonmatch"], r["hist_match"])):
                if n or m:
                    out.append(f"| {edges[i]:.3f}-{edges[i+1]:.3f} | {n} | {m} |")
            out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", nargs="+",
                    default=["historical_50k", "dblp_scholar"])
    ap.add_argument("--out", default="fs-histograms.json")
    ap.add_argument("--summary-md", default="")
    ap.add_argument(
        "--shipped", action="store_true",
        help="probe the SHIPPED configuration: keep the specialised name "
             "scorers instead of rewriting them to jaro_winkler. The rewrite "
             "is right for a Splink-comparable number and wrong as a statement "
             "of what GoldenMatch does; on the head-to-head panel it alone "
             "costs the person shape 0.078 F1, all of it recall.")
    args = ap.parse_args()

    os.environ.setdefault("GOLDENMATCH_FS_CALIBRATED", "posterior")
    os.environ.setdefault("GOLDENMATCH_FS_NATIVE", "1")
    os.environ.setdefault("GOLDENMATCH_FS_CALIBRATE_THRESHOLD", "1")

    mode = "SHIPPED" if args.shipped else "splink-comparable"
    print(f"[hist] scorer mode: {mode}", flush=True)

    reports: list[dict] = []
    for name in args.datasets:
        r: dict
        try:
            r = collect(name, basic_scorers=not args.shipped)
        except Exception as exc:  # noqa: BLE001 - one dataset must not lose the rest
            r = {"dataset": name, "error": str(exc)[:300],
                 "basic_scorers": not args.shipped}
        reports.append(r)
        if r.get("error"):
            print(f"[hist] {name}: ERROR {r['error']}", flush=True)
        else:
            ov = r.get("overlap") or {}
            print(f"[hist] {name}: {r.get('n_training_pairs', 0):,} pairs, "
                  f"chose {r.get('chosen_threshold')} "
                  f"({r.get('threshold_source')}), separable={ov.get('separable')}",
                  flush=True)

    Path(args.out).write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"[hist] wrote {args.out}", flush=True)
    if args.summary_md:
        Path(args.summary_md).write_text(render(reports), encoding="utf-8")
        print(f"[hist] wrote {args.summary_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
