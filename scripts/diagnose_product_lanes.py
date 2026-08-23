"""Where the F1 goes on the Leipzig product benchmarks, one lane at a time (#2717).

`scripts/run_benchmarks.py` reports the end-to-end F1 of each lane. When that
number is bad it does not say WHICH stage lost the pairs, and #2717 spent a lot
of effort on stage attribution done by hand. This does it in one run, for both
lanes, against the same ground truth the benchmark scores on:

  1. **blocking** -- how many candidate pairs the block scorer saw, and what
     fraction of the ground-truth pairs are among them. This is a hard ceiling
     on everything downstream.
  2. **scoring** -- the score distribution over those candidates, and the
     precision/recall/F1 the pair set would reach at each cut. The committed
     matchkey threshold is marked, so "the cut is in the wrong place" and "the
     candidates were never there" stop looking alike.
  3. **emission** -- what the run actually returned, so a gap between "pairs
     above the cut" and "pairs emitted" is visible rather than inferred.

Both lanes are measured because they are different tasks with different ground
truth -- see `dqbench_adapters.leipzig_eval.run_two_source_link_zeroconfig`.

Usage (from the repo root):

    python scripts/diagnose_product_lanes.py --dataset amazon-google
    python scripts/diagnose_product_lanes.py --dataset abt-buy --lane linkage

It imports `run_benchmarks._PRODUCT_SPECS` so the file names, column renames and
ground-truth columns can never drift from what the benchmark measures.

LIMITATION, stated because it changes how the numbers read: the capture hooks
the WEIGHTED block scorer, so `candidate pairs scored` and the P/R/F1 curve
describe the fuzzy-matchkey path only. Pairs from an EXACT matchkey never pass
through it. On Abt-Buy that is most of the signal -- the curve tops out around
F1 0.11 while the run emits F1 0.57 -- so read a large gap between the curve and
`ACTUALLY EMITTED` as "the exact matchkey is carrying this", not as a defect.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_benchmarks import _PRODUCT_SPECS  # type: ignore[import-not-found]  # noqa: E402

DEFAULT_DATASETS_DIR = Path("packages/python/goldenmatch/tests/benchmarks/datasets")

#: Cuts to report the P/R/F1 curve at. Deliberately spans well below the
#: adaptive-threshold floor so "the optimum is under the floor" is visible
#: rather than clipped away.
_CUTS = [0.30, 0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


def _load(datasets_dir: Path, key: str) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    spec = _PRODUCT_SPECS[key]
    base = datasets_dir / spec["subdir"]
    a = pl.read_csv(base / spec["file_a"], encoding="utf8-lossy", ignore_errors=True)
    b = pl.read_csv(base / spec["file_b"], encoding="utf8-lossy", ignore_errors=True)
    rename = spec["rename"] or {}
    a = a.rename({k: v for k, v in rename.items() if k in a.columns})
    b = b.rename({k: v for k, v in rename.items() if k in b.columns})
    gt = pl.read_csv(base / spec["gt_file"], encoding="utf8-lossy", ignore_errors=True)
    merged = dict(spec)
    merged["gt"] = gt
    return a, b, merged


def _prf(found: set, truth: set) -> tuple[float, float, float]:
    tp = len(found & truth)
    fp = len(found - truth)
    fn = len(truth - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return p, r, f1


def _capture_scored_pairs() -> tuple[list[list[tuple[int, int, float]]], Any]:
    """Patch the block scorer so every scored pair is captured BEFORE the cut.

    The scorer applies the matchkey threshold itself and returns only survivors,
    so a shipped result cannot answer "what would a different cut have given".
    This runs the SAME scorer the pipeline picked, at threshold 0.0, and keeps
    every pair -- it is not a re-implementation of the scoring, which is the
    trap that produced two wrong candidate counts earlier in #2717.

    Returns one BATCH PER SCORING PASS, not one flat list. The auto-config
    controller runs the pipeline on a sample several times before the final
    full run, and below the sampling floor the "sample" is the whole frame --
    so a flat list is the UNION of several different candidate configurations
    and describes no single run. Only the last batch is the shipped one; the
    first flat version of this harness inflated blocking recall by pooling
    them.
    """
    import goldenmatch.core.pipeline as pipeline_mod

    batches: list[list[tuple[int, int, float]]] = []
    original = pipeline_mod._get_block_scorer

    def patched(config: Any):
        scorer = original(config)

        def wrapper(blocks: Any, mk: Any, matched_pairs: Any, **kwargs: Any):
            open_mk = mk.model_copy(update={"threshold": 0.0})
            pairs = scorer(blocks, open_mk, matched_pairs, **kwargs)
            batches.append(list(pairs))
            return [p for p in pairs if p[2] >= (mk.threshold or 0.0)]

        return wrapper

    pipeline_mod._get_block_scorer = patched
    return batches, (pipeline_mod, original)


def _final_batch(batches: list[list[tuple[int, int, float]]]) -> list:
    """The scoring pass of the SHIPPED run (the last one)."""
    if not batches:
        return []
    print(f"scoring passes seen    : {len(batches)} "
          f"(sizes {[len(b) for b in batches]}); reporting the LAST one")
    return batches[-1]


def _restore(handle: Any) -> None:
    module, original = handle
    module._get_block_scorer = original


def _committed_threshold() -> float | None:
    """The weighted matchkey threshold the controller actually committed."""
    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN

    run = _LAST_CONTROLLER_RUN.get()
    if not run:
        return None
    _, history = run
    for entry in reversed(history.entries):
        for mk in entry.config.get_matchkeys() or []:
            if mk.type == "weighted" and mk.threshold is not None:
                return float(mk.threshold)
    return None


def _report(
    label: str,
    captured: list[tuple[int, int, float]],
    pair_to_ids: Any,
    truth: set,
    committed_threshold: float | None,
    emitted: set,
) -> None:
    print("")
    print("=== " + label + " ===")
    candidates = {ids for ids in (pair_to_ids(a, b) for a, b, _ in captured) if ids is not None}
    print(f"candidate pairs scored : {len(captured)} ({len(candidates)} distinct id-pairs)")
    print(f"ground-truth pairs     : {len(truth)}")
    if truth:
        print(
            f"BLOCKING RECALL        : {len(candidates & truth) / len(truth):.4f}"
            "   <- hard ceiling on every number below"
        )

    if not captured:
        print("no pairs scored -- nothing downstream can be attributed")
        return

    scores = sorted(s for _, _, s in captured)
    print(
        f"score min/median/max   : {scores[0]:.4f} / "
        f"{scores[len(scores) // 2]:.4f} / {scores[-1]:.4f}"
    )

    print("")
    print(" cut     precision  recall     F1       pairs")
    best_f1, best_cut = 0.0, None
    for cut in _CUTS:
        found = {
            ids
            for ids in (pair_to_ids(a, b) for a, b, s in captured if s >= cut)
            if ids is not None
        }
        p, r, f1 = _prf(found, truth)
        mark = ""
        if committed_threshold is not None and abs(cut - committed_threshold) < 0.005:
            mark = "  <- committed"
        print(f" {cut:.2f}    {p:.4f}     {r:.4f}    {f1:.4f}   {len(found):>6}{mark}")
        if f1 > best_f1:
            best_f1, best_cut = f1, cut
    if best_cut is not None:
        print("")
        print(f"best cut on this candidate set: {best_cut:.2f} (F1 {best_f1:.4f})")

    p, r, f1 = _prf(emitted, truth)
    print(
        f"ACTUALLY EMITTED       : {len(emitted)} pairs, "
        f"precision={p:.4f} recall={r:.4f} f1={f1:.4f}"
    )
    if committed_threshold is not None:
        above = {
            ids
            for ids in (pair_to_ids(a, b) for a, b, s in captured if s >= committed_threshold)
            if ids is not None
        }
        if len(above) != len(emitted):
            print(
                f"  NOTE: {len(above)} pairs score >= the committed "
                f"{committed_threshold:.2f} but {len(emitted)} were emitted -- "
                "a stage after scoring is moving the effective cut."
            )


def run_dedupe_lane(a: pl.DataFrame, b: pl.DataFrame, spec: dict) -> None:
    from dqbench_adapters.leipzig_eval import (  # type: ignore[import-not-found]
        _connected_components,
        _within_cluster_pairs,
    )
    from goldenmatch import dedupe_df

    src_a, src_b = spec["src_a"], spec["src_b"]
    shared = [c for c in a.columns if c in b.columns and c != "id"]

    def prep(df: pl.DataFrame, src: str) -> pl.DataFrame:
        return (
            df.select(["id"] + shared)
            .with_columns((pl.lit(src + ":") + pl.col("id").cast(pl.Utf8)).alias("record_id"))
            .drop("id")
        )

    records = pl.concat([prep(a, src_a), prep(b, src_b)])
    all_ids = records["record_id"].to_list()

    ca, cb = spec["gt_cols"]
    gt_pairs = {
        (src_a + ":" + str(row[ca]).strip(), src_b + ":" + str(row[cb]).strip())
        for row in spec["gt"].to_dicts()
    }
    truth = _within_cluster_pairs(_connected_components(all_ids, gt_pairs))

    def pair_to_ids(x: int, y: int):
        if not (0 <= x < len(all_ids) and 0 <= y < len(all_ids)):
            return None
        u, v = all_ids[x], all_ids[y]
        return (u, v) if u <= v else (v, u)

    batches, handle = _capture_scored_pairs()
    try:
        result = dedupe_df(records)
    finally:
        _restore(handle)
    captured = _final_batch(batches)

    assign: dict[str, str] = {}
    for cid, cluster in (getattr(result, "clusters", None) or {}).items():
        members = cluster["members"] if isinstance(cluster, dict) else cluster.members
        for row_id in members:
            assign[all_ids[row_id]] = str(cid)
    emitted = _within_cluster_pairs(assign)

    _report(
        "DEDUPE lane (concatenated frame, transitive-closure truth)",
        captured,
        pair_to_ids,
        truth,
        _committed_threshold(),
        emitted,
    )

    if captured:
        same_source = sum(
            1
            for x, y, _ in captured
            if 0 <= x < len(all_ids)
            and 0 <= y < len(all_ids)
            and all_ids[x].split(":")[0] == all_ids[y].split(":")[0]
        )
        print(
            f"same-source candidates : {same_source}/{len(captured)} "
            f"({same_source / len(captured):.1%}) -- unmatchable against a "
            "cross-source mapping"
        )


def run_linkage_lane(a: pl.DataFrame, b: pl.DataFrame, spec: dict) -> None:
    from goldenmatch import match_df

    src_a, src_b = spec["src_a"], spec["src_b"]
    shared = [c for c in a.columns if c in b.columns and c != "id"]
    ids_a = a["id"].cast(pl.Utf8).to_list()
    ids_b = b["id"].cast(pl.Utf8).to_list()
    n_a = len(ids_a)

    ca, cb = spec["gt_cols"]
    truth = {
        (src_a + ":" + str(row[ca]).strip(), src_b + ":" + str(row[cb]).strip())
        for row in spec["gt"].to_dicts()
    }

    def pair_to_ids(x: int, y: int):
        # Reference row ids are offset by the target height; either orientation.
        lo, hi = (x, y) if x < y else (y, x)
        if not (0 <= lo < n_a and n_a <= hi < n_a + len(ids_b)):
            return None  # same-source pair: not expressible as a linkage pair
        return (src_a + ":" + ids_a[lo], src_b + ":" + ids_b[hi - n_a])

    batches, handle = _capture_scored_pairs()
    try:
        result = match_df(a.select(shared), b.select(shared))
    finally:
        _restore(handle)
    captured = _final_batch(batches)

    emitted: set[tuple[str, str]] = set()
    matched = getattr(result, "matched", None)
    if matched is not None and not hasattr(matched, "height"):
        matched = pl.from_arrow(matched)
    if matched is not None and matched.height:
        for row in matched.iter_rows(named=True):
            ids = pair_to_ids(row["__target_row_id__"], row["__ref_row_id__"])
            if ids is not None:
                emitted.add(ids)

    _report(
        "LINKAGE lane (match_df, raw cross-source mapping as truth)",
        captured,
        pair_to_ids,
        truth,
        _committed_threshold(),
        emitted,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="amazon-google", choices=sorted(_PRODUCT_SPECS))
    parser.add_argument("--datasets-dir", type=Path, default=DEFAULT_DATASETS_DIR)
    parser.add_argument("--lane", default="both", choices=["both", "dedupe", "linkage"])
    args = parser.parse_args()

    base = args.datasets_dir / _PRODUCT_SPECS[args.dataset]["subdir"]
    if not base.is_dir():
        print(
            f"dataset not found under {base} -- run scripts/run_benchmarks.py "
            f"--datasets {args.dataset} once to fetch it"
        )
        return 1

    a, b, spec = _load(args.datasets_dir, args.dataset)
    shared = [c for c in a.columns if c in b.columns and c != "id"]
    print(f"{args.dataset}: {a.height} x {b.height} rows, shared columns {shared}")
    if args.lane in ("both", "dedupe"):
        run_dedupe_lane(a, b, spec)
    if args.lane in ("both", "linkage"):
        run_linkage_lane(a, b, spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
