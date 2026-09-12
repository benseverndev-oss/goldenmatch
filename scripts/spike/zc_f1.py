"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

Zero-config F1 per dataset, nothing else: `dedupe_df(df, confidence_required=False)` in a child
process per dataset. Used to measure the three zero-config fixes against origin/main on the design
corpus, by running it from a checkout of each code state with scripts/spike checked out on top.

Usage, from the repo root:
  python -m scripts.spike.zc_f1 --out DIR [--datasets a,b]
"""

from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_DETERMINISTIC", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

_LAST = ("musicbrainz_20k", "dblp_scholar", "historical_50k")


def child(name: str, out: Path) -> None:
    import goldenmatch
    from goldenmatch.core.evaluate import evaluate_clusters

    from scripts.autoconfig_quality.rule_sweep import resolve_loader

    record: dict = {"dataset": name}
    t0 = time.perf_counter()
    loaded = resolve_loader(name)()
    if loaded is None or not loaded[1]:
        record["skipped"] = "unavailable or unlabelled"
    else:
        df, gt = loaded
        gt = {(min(a, b), max(a, b)) for a, b in gt}
        record.update(rows=df.height, gt_pairs=len(gt))
        try:
            result = goldenmatch.dedupe_df(df, confidence_required=False)
            s = evaluate_clusters(result.clusters, gt).summary()
            blocking = getattr(result.config, "blocking", None)
            record.update(
                f1=s["f1"],
                precision=s["precision"],
                recall=s["recall"],
                scored_pairs=len(result.scored_pairs or []),
                matchkey_types=sorted({mk.type for mk in result.config.get_matchkeys()}),
                blocking_strategy=getattr(blocking, "strategy", None),
                backend=getattr(result.config, "backend", None),
            )
        except Exception as exc:
            record.update(
                error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-2000:]
            )
    record["seconds"] = round(time.perf_counter() - t0, 1)
    (out / f"{name}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="THROWAWAY: zero-config F1 per dataset.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--child", default=None)
    ap.add_argument("--datasets", default="")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.child:
        child(args.child, args.out)
        return 0
    from scripts.autoconfig_quality.corpus import ci_datasets

    names = [d.strip() for d in args.datasets.split(",") if d.strip()] or ci_datasets("design")
    names = [n for n in names if n not in _LAST] + [n for n in _LAST if n in names]
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    for name in names:
        if (args.out / f"{name}.json").exists():
            continue
        t0 = time.perf_counter()
        argv_child = [
            sys.executable,
            "-m",
            "scripts.spike.zc_f1",
            "--child",
            name,
            "--out",
            str(args.out),
        ]
        try:
            rc = subprocess.run(argv_child, env=env, timeout=5400).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        if not (args.out / f"{name}.json").exists():
            (args.out / f"{name}.json").write_text(
                json.dumps({"dataset": name, "crashed": f"rc={rc}"}), encoding="utf-8"
            )
        print(f"[zc-f1] {name}: rc={rc} {time.perf_counter() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
