"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

fs_loss_probe found forced-FS reachable recall (true pairs that got any score) far below the blocking
recall it rebuilds from ``build_blocks`` on the raw frame: amazon_google 0.081 vs 0.952. Either the FS
scorer drops candidates before any threshold, or the rebuilt blocks are not the pipeline's blocks.

This runs forced FS (review floor near zero) with GOLDENMATCH_BENCH_DUMP_PAIRS set, so the pipeline
writes ITS OWN within-block candidate set and its linked set, then compares four sets against the truth:
pipeline candidates, linked pairs, every returned pair (linked + review), and the raw-frame rebuild.

Usage, from the repo root:
  python -m scripts.spike.floor_probe --out DIR --datasets a,b
"""

from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402


def _recall(pairs: set, gt: set) -> float | None:
    return round(len(pairs & gt) / len(gt), 4) if gt else None


def child(name: str, out: Path) -> None:
    import goldenmatch
    import pyarrow.parquet as pq
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

    from scripts.autoconfig_quality.rule_sweep import resolve_loader
    from scripts.spike.fs_loss_probe import _REVIEW_FLOOR, _blocking_recall, _canon, _cluster

    df, gt = resolve_loader(name)()
    gt = {(min(a, b), max(a, b)) for a, b in gt}
    cfg = auto_configure_probabilistic_df(df)
    mks = [
        mk.model_copy(update={"review_threshold": _REVIEW_FLOOR})
        if mk.type == "probabilistic"
        else mk
        for mk in cfg.get_matchkeys()
    ]
    cfg = cfg.model_copy(update={"matchkeys": mks, "match_settings": None})

    dump = out / f"{name}-dump"
    os.environ["GOLDENMATCH_BENCH_DUMP_PAIRS"] = str(dump)
    t0 = time.perf_counter()
    result = goldenmatch.dedupe_df(df, config=cfg, confidence_required=False)
    seconds = round(time.perf_counter() - t0, 1)
    del os.environ["GOLDENMATCH_BENCH_DUMP_PAIRS"]

    def pairs(file: str) -> set:
        table = pq.read_table(dump / file)
        return set(zip(table.column("a").to_pylist(), table.column("b").to_pylist()))

    candidates = pairs("candidate_pairs.parquet")
    linked_dump = pairs("emitted_pairs.parquet")
    returned = set(_canon(result.scored_pairs or [])) | set(_canon(result.review_pairs or []))
    record = {
        "dataset": name,
        "rows": df.height,
        "gt_pairs": len(gt),
        **_cluster(result, gt),
        "pipeline_candidates": len(candidates),
        "pipeline_blocking_recall": _recall(candidates, gt),
        "linked_pairs": len(linked_dump),
        "linked_recall": _recall(linked_dump, gt),
        "returned_pairs": len(returned),
        "returned_recall": _recall(returned, gt),
        "returned_not_in_candidates": len(returned - candidates),
        "candidates_not_returned": len(candidates - returned),
        "true_candidates_not_returned": len((candidates & gt) - returned),
        "rebuilt": _blocking_recall(df, cfg, gt),
        "blocking": cfg.blocking.model_dump(mode="json", exclude_defaults=True)
        if cfg.blocking
        else None,
        "link_thresholds": {
            k: {f: v.get(f) for f in ("link_threshold", "cut_rule", "source")}
            for k, v in ((result.stats or {}).get("fs_link_thresholds") or {}).items()
        },
        "seconds": seconds,
    }
    (out / f"{name}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="THROWAWAY: is the FS reachable-recall gap a scorer floor?"
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--child", default=None)
    ap.add_argument("--datasets", default="")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.child:
        child(args.child, args.out)
        return 0
    env = {**os.environ, "PYTHONHASHSEED": "0", "GOLDENMATCH_AUTOCONFIG_MEMORY": "0"}
    for name in [d.strip() for d in args.datasets.split(",") if d.strip()]:
        if (args.out / f"{name}.json").exists():
            continue
        t0 = time.perf_counter()
        argv_child = [
            sys.executable,
            "-m",
            "scripts.spike.floor_probe",
            "--child",
            name,
            "--out",
            str(args.out),
        ]
        try:
            rc = subprocess.run(argv_child, env=env, timeout=5400).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        print(f"[floor] {name}: rc={rc} {time.perf_counter() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
