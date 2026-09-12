"""THROWAWAY spike (branch spike/fs-loss-decomposition) -- never merge.

Why does zero-config dedupe_df lose to forced FS? For each named dataset, in its own child process:
call ``dedupe_df(df)`` exactly as a user would (confidence_required default) and record whether it
refuses, then run it with ``confidence_required=False`` and dump the config zero-config chose
(blocking, matchkeys), cluster P/R/F1 and the scalar run stats.

Usage, from the repo root:
  python -m scripts.spike.zc_config_dump --out DIR --datasets a,b
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
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402


def _scalar_stats(stats: dict) -> dict:
    out = {}
    for key, value in (stats or {}).items():
        if isinstance(value, (int, float, bool, str)) or value is None:
            out[key] = value if not isinstance(value, str) else value[:300]
        else:
            text = json.dumps(value, default=str)
            out[key] = (
                json.loads(text)
                if len(text) <= 4000
                else f"<{type(value).__name__} {len(text)} chars>"
            )
    return out


def child(name: str, out: Path) -> None:
    import goldenmatch

    from scripts.autoconfig_quality.rule_sweep import resolve_loader
    from scripts.spike.fs_loss_probe import _cluster

    record: dict = {"dataset": name}
    df, gt = resolve_loader(name)()
    gt = {(min(a, b), max(a, b)) for a, b in gt}
    record.update(rows=df.height, gt_pairs=len(gt))

    t0 = time.perf_counter()
    try:
        default = goldenmatch.dedupe_df(df)
        record["default_call"] = {"outcome": "ran", **_cluster(default, gt)}
    except Exception as exc:
        record["default_call"] = {
            "outcome": "raised",
            "error": f"{type(exc).__name__}: {exc}"[:1000],
        }
    record["default_call"]["seconds"] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    try:
        result = goldenmatch.dedupe_df(df, confidence_required=False)
        record["zero_config"] = {
            **_cluster(result, gt),
            "config": result.config.model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            ),
            "stats": _scalar_stats(result.stats),
            "clusters_ge2": sum(
                1 for c in result.clusters.values() if len(c.get("members", [])) > 1
            ),
            "scored_pairs": len(result.scored_pairs or []),
        }
    except Exception as exc:
        record["zero_config"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-3000:],
        }
    record["zero_config"]["seconds"] = round(time.perf_counter() - t0, 1)
    (out / f"{name}.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="THROWAWAY: dump the config zero-config chose.")
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
            "scripts.spike.zc_config_dump",
            "--child",
            name,
            "--out",
            str(args.out),
        ]
        try:
            rc = subprocess.run(argv_child, env=env, timeout=5400).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
        print(f"[zc-dump] {name}: rc={rc} {time.perf_counter() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
