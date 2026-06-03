"""Stage E: out-of-core spill bench for the DataFusion spine.

Measures whether the spine's RELATIONAL stages (score self-join + dedup
GROUP BY, inside one DataFusion ctx with a fair-spill pool) SPILL and
SURVIVE where the in-memory pipeline OOMs. Three variants per scale:

    bucket         -- dedupe_df(df, backend="bucket"): the in-memory
                      production winner (holds within-block score state
                      in RAM); the OOM comparand.
    spine_nospill  -- run_spine(blocks, cfg, memory_limit=None): the
                      spine with NO pool cap.
    spine_spill    -- run_spine(blocks, cfg, memory_limit=POOL): the
                      spine with a low fair-spill pool so score+dedup
                      spill to disk.

Each variant runs in a SUBPROCESS (this script re-invoked with --worker)
so a Linux OOM-kill of one variant is recorded as a non-zero exit, not a
bench crash (mirrors bench-pipeline-complete-path.yml). The child
self-reports peak RSS via getrusage(RUSAGE_SELF).ru_maxrss.

SCOPE (honest): the spill-survival claim is for the relational stages
ONLY. The spine's UF break (build_cluster_frames) collects raw pairs to
the driver -- an in-memory island the spill pool does NOT cover. Keep
scale below the ~50M-pair scipy/UF envelope (person-shape data yields
~8.3M pairs at 25M rows, so 25-40M rows is the OOM-seeking-yet-safe
zone). Pushing past 50M pairs OOMs the UF island, not the relational
stages -- a FALSE negative. If nothing OOMs at reachable scale, that's a
valid HONEST-NULL result (the spine's value is then engine portability).

Usage (CI / large-new-64GB only -- OOMs a laptop):
    python scripts/bench_datafusion_spine_spill.py \
        --rows 5000000,25000000 --pool-mb 8192 --out result.json
    python scripts/bench_datafusion_spine_spill.py --smoke   # tiny, CI
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

# realistic_person_df lives under tests/fixtures (same sys.path dance as
# bench_datafusion_vs_bucket.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from fixtures.realistic_person import realistic_person_df  # noqa: E402
from goldenmatch import dedupe_df  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

VARIANTS = ("bucket", "spine_nospill", "spine_spill")


def _spine_config() -> GoldenMatchConfig:
    """Scale-mode spine config: single-field weighted jaro_winkler on
    last_name, soundex static blocking -- the supported spine surface
    (mode='scale' is REQUIRED by the Stage D gate)."""
    return GoldenMatchConfig(
        mode="scale",
        matchkeys=[
            MatchkeyConfig(
                name="last_name_fuzzy",
                type="weighted",
                fields=[MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.85,
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"], transforms=["soundex"])],
        ),
    )


def _bucket_config() -> GoldenMatchConfig:
    """Same matchkey/blocking as the spine, backend=bucket (standard
    mode) -- the in-memory comparand on an identical workload."""
    cfg = _spine_config()
    cfg.mode = "standard"
    cfg.backend = "bucket"
    return cfg


def _build_blocks(df: pl.DataFrame, config: GoldenMatchConfig):
    """Mirror tests/test_datafusion_spine_parity.py::_prepared_blocks."""
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.matchkey import precompute_matchkey_transforms

    with_ids = df.with_row_index("__row_id__")
    augmented = precompute_matchkey_transforms(with_ids, config.get_matchkeys())
    return build_blocks(augmented.lazy(), config.blocking)


def _worker(variant: str, data_path: str, pool_mb: int) -> int:
    """Run ONE variant in this (child) process. Print a JSON result line
    to stdout and exit 0; on OOM the OS kills us and the parent records
    it. Peak RSS is self-reported via getrusage."""
    df = pl.read_parquet(data_path)
    t0 = time.perf_counter()

    if variant == "bucket":
        result = dedupe_df(df, config=_bucket_config())
        pairs = result.dupes.height if result.dupes is not None else 0
        clusters = len(result.clusters) if result.clusters else 0
    else:
        from goldenmatch.backends.datafusion_spine import run_spine

        cfg = _spine_config()
        blocks = _build_blocks(df, cfg)
        pool = None if variant == "spine_nospill" else pool_mb * 1024 * 1024
        _golden, assign, raw_pairs = run_spine(blocks, cfg, memory_limit=pool)
        pairs = len(raw_pairs)
        clusters = assign["cluster_id"].n_unique() if assign is not None and assign.height else 0

    wall = time.perf_counter() - t0
    # ru_maxrss: Linux = KB, macOS = bytes. CI is Linux -> KB -> MB.
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(json.dumps({
        "variant": variant, "wall_s": wall, "peak_rss_mb": peak_mb,
        "pairs": int(pairs), "clusters": int(clusters), "status": "ok",
    }))
    return 0


def _run_variant_subprocess(variant: str, data_path: str, pool_mb: int) -> dict:
    """Spawn a child for one variant; capture its JSON or record OOM."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()),
         "--worker", variant, "--data", data_path, "--pool-mb", str(pool_mb)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # -9 (POSIX SIGKILL) / 137 (128+9 via shell) = the OOM-killer signature.
        oom = proc.returncode in (137, -9)
        return {
            "variant": variant,
            "status": "OOM" if oom else "ERROR",
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr[-2000:],
        }
    # The child may emit warnings before the JSON line; take the last
    # non-empty stdout line as the result.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"variant": variant, "status": "ERROR", "returncode": 0,
                "stderr_tail": "no JSON emitted; stderr: " + proc.stderr[-1500:]}
    return json.loads(lines[-1])


def _bench_scale(rows: int, pool_mb: int, seed: int, tmp: Path) -> dict:
    data_path = tmp / f"spine_spill_{rows}.parquet"
    df = realistic_person_df(rows, seed=seed)
    df.write_parquet(data_path)
    del df  # free the parent's copy before spawning children
    print(f"\n=== rows={rows:,} pool={pool_mb}MB (data={data_path.name}) ===", flush=True)
    out: dict[str, dict] = {}
    for variant in VARIANTS:
        r = _run_variant_subprocess(variant, str(data_path), pool_mb)
        out[variant] = r
        if r.get("status") == "ok":
            print(f"  {variant}: wall={r['wall_s']:.1f}s peak_rss={r['peak_rss_mb']:.0f}MB "
                  f"pairs={r['pairs']} clusters={r['clusters']}", flush=True)
        else:
            print(f"  {variant}: {r['status']} (rc={r.get('returncode')})", flush=True)
    try:
        data_path.unlink()
    except OSError:
        pass
    return {"rows": rows, "pool_mb": pool_mb, "results": out}


def _markdown(scales: list[dict]) -> str:
    lines = ["## bench-datafusion-spine-spill", "",
             "| rows | variant | wall_s | peak_rss_MB | pairs | status |",
             "|---|---|---|---|---|---|"]
    for s in scales:
        for variant in VARIANTS:
            r = s["results"][variant]
            if r.get("status") == "ok":
                lines.append(f"| {s['rows']:,} | {variant} | {r['wall_s']:.1f} | "
                             f"{r['peak_rss_mb']:.0f} | {r['pairs']} | ok |")
            else:
                lines.append(f"| {s['rows']:,} | {variant} | - | - | - | "
                             f"**{r['status']}** (rc={r.get('returncode')}) |")
    # Verdict line: binding iff at the largest scale bucket OOMs/errors
    # AND spine_spill is ok.
    top = scales[-1]["results"] if scales else {}
    bucket_dead = top.get("bucket", {}).get("status") in ("OOM", "ERROR")
    spill_ok = top.get("spine_spill", {}).get("status") == "ok"
    verdict = ("BINDING: in-memory (bucket) OOM/errored while spine_spill survived"
               if bucket_dead and spill_ok else
               "HONEST-NULL: nothing OOMed at this scale -- spine value is "
               "engine-portability, not one-box survival (push scale or lower pool "
               "to seek the binding point, staying < 50M pairs)")
    lines += ["", f"**Verdict:** {verdict}"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", choices=VARIANTS, help="internal: run one variant")
    ap.add_argument("--data", help="internal: parquet path for --worker")
    ap.add_argument("--rows", default="5000000,25000000",
                    help="comma-separated row counts (OOM-seeking; keep < 50M pairs)")
    ap.add_argument("--pool-mb", type=int, default=8192,
                    help="fair-spill pool size (MB) for spine_spill")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true",
                    help="tiny scale for CI validation (rows=2000, pool=128MB)")
    ap.add_argument("--out", default=None, help="JSON output path")
    args = ap.parse_args()

    if args.worker:
        return _worker(args.worker, args.data, args.pool_mb)

    rows_list = [2000] if args.smoke else [int(x) for x in args.rows.split(",")]
    pool_mb = 128 if args.smoke else args.pool_mb
    tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "spine-spill-bench"
    tmp.mkdir(parents=True, exist_ok=True)

    scales = [_bench_scale(n, pool_mb, args.seed, tmp) for n in rows_list]
    payload = {"scales": scales, "pool_mb": pool_mb, "smoke": args.smoke}
    md = _markdown(scales)
    print("\n" + md)
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
