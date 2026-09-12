"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

Write each design dataset to <out>/<name>.parquet plus <out>/<name>.gt.json (ground-truth row-index pairs), so
measurement runs against code states that lack the stack's loader dependencies (rule_sweep imports
goldenmatch.core.fs_cut_rules, which origin/main does not have) can read the data without importing any loader.

Usage, from the repo root:
  python -m scripts.spike.materialize_corpus --out DIR [--datasets a,b]
"""

from __future__ import annotations

import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import argparse  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="THROWAWAY: materialize the design corpus.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--datasets", default="")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    from scripts.autoconfig_quality.corpus import ci_datasets
    from scripts.autoconfig_quality.rule_sweep import resolve_loader

    names = [d.strip() for d in args.datasets.split(",") if d.strip()] or ci_datasets("design")
    for name in names:
        loaded = resolve_loader(name)()
        if loaded is None or not loaded[1]:
            print(f"[materialize] {name}: unavailable or unlabelled", flush=True)
            continue
        df, gt = loaded
        df.write_parquet(args.out / f"{name}.parquet")
        pairs = sorted({(min(int(a), int(b)), max(int(a), int(b))) for a, b in gt})
        (args.out / f"{name}.gt.json").write_text(json.dumps(pairs), encoding="utf-8")
        print(f"[materialize] {name}: rows={df.height} gt={len(pairs)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
