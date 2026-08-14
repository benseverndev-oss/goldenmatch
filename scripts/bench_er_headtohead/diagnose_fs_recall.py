"""Where does GM's FS auto-config lose recall on person-shaped data?

Phase 1 instrumentation. The bench says F1 0.553 with precision EXACTLY 1.000
and recall 0.382 -- zero false positives across five billion true negatives.
That is an under-merge, and it can come from three places that need different
fixes:

  1. BLOCKING   -- the true pairs never become candidates at all.
  2. SCORING    -- they are candidates but never clear the threshold.
  3. CLUSTERING -- they clear the threshold and are then torn apart.

So this attributes the loss BEFORE anything is changed. Each stage is a
strictly tighter bound than the one before it, so whichever one drops is the
one at fault.

ID SPACE (the thing my first attempt got wrong): GM works internally in
`__row_id__`, which for this generated fixture IS the positional row index, and
the generator writes `record_id` equal to that index. Block frames carry
`record_id`; cluster `members` and `scored_pairs` carry `__row_id__`. They
coincide here, which is exactly why a mix-up produces a plausible-looking zero
instead of an error.
"""
from __future__ import annotations

import itertools
import os
from collections import defaultdict

# Match the bench lane exactly (orchestrate.py: gm_probabilistic_native).
os.environ["GOLDENMATCH_FS_NATIVE"] = "1"
os.environ["GOLDENMATCH_FS_CALIBRATED"] = "posterior"

import polars as pl  # noqa: E402

BASE = os.environ.get("GM_DIAG_FIXTURE_DIR", ".")
_BASIC = {"jaro_winkler", "levenshtein", "token_sort", "exact"}


def pairs_of(groups) -> set[tuple[int, int]]:
    out = set()
    for members in groups:
        if len(members) > 1:
            for a, b in itertools.combinations(sorted(int(m) for m in members), 2):
                out.add((a, b))
    return out


def col(frame, name):
    """Read a column off the Frame seam.

    `.native` is the underlying polars frame; the seam exposes `.column(name)`
    but `native` keeps this readable and is what the seam is for.
    """
    return list(frame.native[name])


def main() -> int:
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    from goldenmatch.core.blocker import build_blocks

    df = pl.read_parquet(f"{BASE}/p20k.parquet")
    truth = pl.read_parquet(f"{BASE}/p20k.truth.parquet")

    by_cluster = defaultdict(list)
    for rid, cid in zip(truth["record_id"].to_list(), truth["cluster_id"].to_list()):
        by_cluster[cid].append(int(rid))
    tp_set = pairs_of(by_cluster.values())
    print(f"rows={df.height:,}  true pairs={len(tp_set):,}\n")

    cfg = auto_configure_probabilistic_df(df)
    for mk in cfg.get_matchkeys():
        for f in getattr(mk, "fields", None) or []:
            if f.scorer and f.scorer not in _BASIC:
                f.scorer = "jaro_winkler"

    # ── Stage 1: blocking ──
    blocks = build_blocks(df, cfg.blocking)
    cand: set[tuple[int, int]] = set()
    sizes = []
    for b in blocks:
        ids = col(b.materialize(), "record_id")
        sizes.append(len(ids))
        if len(ids) > 1:
            for a, c in itertools.combinations(sorted(int(x) for x in ids), 2):
                cand.add((a, c))
    hit = len(cand & tp_set)
    sizes.sort()
    print("=== stage 1: blocking ===")
    print(f"  blocks={len(blocks):,}  candidate pairs={len(cand):,}")
    print(f"  block sizes: max={sizes[-1]} p50={sizes[len(sizes)//2]} "
          f"singletons={sum(1 for s in sizes if s == 1):,}")
    print(f"  CANDIDATE RECALL = {hit:,}/{len(tp_set):,} = "
          f"{hit/max(len(tp_set),1):.4f}   <- ceiling on everything after\n")

    # ── Stages 2 + 3 ──
    res = dedupe_df(df, config=cfg)

    accepted = {(int(a), int(b)) for a, b, _ in (res.scored_pairs or [])}
    acc_tp = len(accepted & tp_set)
    print("=== stage 2: after scoring, before clustering ===")
    print(f"  accepted pairs={len(accepted):,}")
    print(f"  SCORED RECALL = {acc_tp:,}/{len(tp_set):,} = "
          f"{acc_tp/max(len(tp_set),1):.4f}\n")

    groups = []
    for _cid, c in (res.clusters or {}).items():
        groups.append(c["members"] if isinstance(c, dict) else c.members)
    pred = pairs_of(groups)
    tp = len(pred & tp_set)
    fp = len(pred - tp_set)
    fn = len(tp_set - pred)
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    print("=== stage 3: after clustering ===")
    print(f"  predicted pairs={len(pred):,}  TP={tp:,} FP={fp:,} FN={fn:,}")
    print(f"  precision={prec:.4f} recall={rec:.4f} "
          f"f1={2*prec*rec/max(prec+rec,1e-9):.4f}\n")

    print("=== ATTRIBUTION ===")
    print(f"  lost to BLOCKING:   {len(tp_set)-hit:,}")
    print(f"  lost to SCORING:    {hit-acc_tp:,}")
    print(f"  lost to CLUSTERING: {acc_tp-tp:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
