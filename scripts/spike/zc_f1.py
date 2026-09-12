"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

Zero-config F1 per dataset, nothing else: `dedupe_df(df, confidence_required=False)` in a child
process per dataset. Used to measure the three zero-config fixes against origin/main on the design
corpus. Reads a corpus written by scripts.spike.materialize_corpus (<name>.parquet + <name>.gt.json)
and imports no loader, so it runs against code states that lack the stack's modules.

Usage, from the repo root:
  python -m scripts.spike.zc_f1 --corpus DIR --out DIR [--datasets a,b]
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


def child(name: str, corpus: Path, out: Path) -> None:
    import goldenmatch
    import polars as pl
    from goldenmatch.core.evaluate import evaluate_clusters

    record: dict = {"dataset": name}
    t0 = time.perf_counter()
    df = pl.read_parquet(corpus / f"{name}.parquet")
    gt = {tuple(p) for p in json.loads((corpus / f"{name}.gt.json").read_text(encoding="utf-8"))}
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
            goldenmatch_file=goldenmatch.__file__,
        )
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-2000:])
    record["seconds"] = round(time.perf_counter() - t0, 1)
    (out / f"{name}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="THROWAWAY: zero-config F1 per dataset.")
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--child", default=None)
    ap.add_argument("--datasets", default="")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.child:
        child(args.child, args.corpus, args.out)
        return 0
    names = [d.strip() for d in args.datasets.split(",") if d.strip()] or sorted(
        p.name[: -len(".parquet")] for p in args.corpus.glob("*.parquet")
    )
    names = [n for n in names if n not in _LAST] + [n for n in _LAST if n in names]
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    for name in names:
        target = args.out / f"{name}.json"
        if target.exists():
            continue
        t0 = time.perf_counter()
        argv_child = [
            sys.executable,
            "-m",
            "scripts.spike.zc_f1",
            "--child",
            name,
            "--corpus",
            str(args.corpus),
            "--out",
            str(args.out),
        ]
        try:
            rc = subprocess.run(argv_child, env=env, timeout=5400).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        if not target.exists():
            target.write_text(
                json.dumps({"dataset": name, "crashed": f"rc={rc}"}), encoding="utf-8"
            )
        print(f"[zc-f1] {name}: rc={rc} {time.perf_counter() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
