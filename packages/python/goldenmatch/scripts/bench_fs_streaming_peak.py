"""Scale-peak bench for the out-of-core STREAMING FS dedupe path.

Generates N synthetic person rows as a parquet file (vectorized, chunked — bounded
generation memory), then runs `dedupe_to_parquet` in one of two modes while a
background thread samples VmRSS:

  --mode streaming   GOLDENMATCH_FS_OUT_OF_CORE=1  (the bounded out-of-core path)
  --mode in-memory   GOLDENMATCH_FS_OUT_OF_CORE=0  (the classic in-memory pipeline)

The 50M PROOF: in-memory OOMs on 64 GB (~82 GB peak, killed → exit 137) while
streaming COMPLETES with a bounded peak. Scale-out (100M/200M/300M) uses NARROW
columns so the still-un-streamed prep frame (~1x frame) stays under 64 GB and the
BACK-HALF scaling (frame-free + DuckDB spill + golden batch + pair spill + the
Rust streaming Union-Find) is what's exercised.

Prints one machine-greppable line on success:
  [bench] RESULT mode=<m> rows=<n> status=completed wall=<s> peak_mb=<mb> \
          unique=<u> dupes=<d> golden=<g> pairs=<p>
On OOM the process is killed by the kernel (no RESULT line) — the caller reads the
exit code (137) as the in-memory baseline evidence.

Usage: python scripts/bench_fs_streaming_peak.py --rows 50000000 --mode streaming
"""
from __future__ import annotations

import argparse
import os
import threading
import time

# Scale knobs the pipeline docs require for any at-scale run (root CLAUDE.md).
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
os.environ.setdefault("_RJEM_MALLOC_CONF", "dirty_decay_ms:1000,muzzy_decay_ms:0")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
os.environ.setdefault("GOLDENMATCH_FS_OOC_DEBUG", "1")


def _vmrss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


class RssSampler(threading.Thread):
    def __init__(self, interval: float = 0.25):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_mb = 0.0
        self._stopev = threading.Event()
        self.t0 = time.perf_counter()

    def run(self):
        last = 0.0
        while not self._stopev.is_set():
            rss = _vmrss_mb()
            t = time.perf_counter() - self.t0
            if rss > self.peak_mb:
                self.peak_mb = rss
            if t - last >= 15.0:
                print(f"[rss] t={t:6.0f}s  {rss:8.0f} MB", flush=True)
                last = t
            self._stopev.wait(self.interval)

    def stop(self):
        self._stopev.set()
        self.join(timeout=3)


# Small value pools so within-block name pairs actually score as matches.
_FIRSTS = ["John", "Jon", "Jane", "Janet", "Bob", "Robert", "Alice", "Alicia",
           "Tom", "Thomas", "Amy", "Amie", "Mary", "Maria", "Bill", "William",
           "Sue", "Susan", "Jim", "James", "Kate", "Katherine", "Ed", "Edward"]
_LASTS = ["Smith", "Smyth", "Doe", "Jones", "Brown", "Wilson", "Clark", "Davis",
          "Miller", "Garcia", "Martinez", "Lee", "Walker", "Hall", "Young", "King",
          "Wright", "Lopez", "Hill", "Green", "Adams", "Baker", "Nelson", "Carter"]


def _write_parquet(n: int, path: str, filler_cols: int, avg_block: int = 8,
                   chunk: int = 5_000_000) -> None:
    """Write N person rows to `path` (parquet) in vectorized numpy chunks. `zip`
    is drawn from a pool of ~N/avg_block distinct values so blocks average
    `avg_block` rows — enough to form real candidate pairs + multi-member clusters
    without exploding into megablocks. `filler_cols` widens the row (0 = the
    narrow scale-out shape; ~8-10 = the representative width that OOMs in-memory)."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(1234)
    firsts = np.array(_FIRSTS, dtype=object)
    lasts = np.array(_LASTS, dtype=object)
    n_zip = max(1, n // avg_block)

    writer = None
    written = 0
    try:
        while written < n:
            m = min(chunk, n - written)
            zp = rng.integers(0, n_zip, m, dtype=np.int64)
            cols = {
                "first_name": pa.array(rng.choice(firsts, m)),
                "last_name": pa.array(rng.choice(lasts, m)),
                "zip": pa.array(np.char.zfill((zp + 10_000).astype("U7"), 7)),
            }
            for k in range(filler_cols):
                vals = rng.integers(0, 1_000_000, m, dtype=np.int64)
                cols[f"attr_{k}"] = pa.array(np.char.zfill(vals.astype("U6"), 6))
            tbl = pa.table(cols)
            if writer is None:
                writer = pq.ParquetWriter(path, tbl.schema)
            writer.write_table(tbl)
            written += m
            print(f"[gen] wrote {written:,}/{n:,} rows", flush=True)
            del tbl, cols
    finally:
        if writer is not None:
            writer.close()


def _fs_config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    blocking = BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["zip"])])
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking, backend="bucket")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--mode", choices=["streaming", "in-memory"], default="streaming")
    ap.add_argument("--filler-cols", type=int, default=8)
    ap.add_argument("--avg-block", type=int, default=8)
    ap.add_argument("--data-dir", default=None, help="dir for the generated parquet + output")
    ap.add_argument("--reuse-data", action="store_true", help="reuse people.parquet if present")
    args = ap.parse_args()

    os.environ["GOLDENMATCH_FS_OUT_OF_CORE"] = "1" if args.mode == "streaming" else "0"

    import tempfile
    data_dir = args.data_dir or tempfile.mkdtemp(prefix="gm_fs_scale_")
    os.makedirs(data_dir, exist_ok=True)
    pq_path = os.path.join(data_dir, "people.parquet")
    out_dir = os.path.join(data_dir, "out")

    if not (args.reuse_data and os.path.exists(pq_path)):
        print(f"[gen] generating {args.rows:,} rows "
              f"(filler_cols={args.filler_cols}, avg_block={args.avg_block}) -> {pq_path}",
              flush=True)
        t_gen = time.perf_counter()
        _write_parquet(args.rows, pq_path, args.filler_cols, args.avg_block)
        print(f"[gen] done in {time.perf_counter()-t_gen:.0f}s", flush=True)

    from goldenmatch import dedupe_to_parquet

    cfg = _fs_config()
    sampler = RssSampler()
    sampler.start()
    base = _vmrss_mb()
    print(f"[bench] mode={args.mode} rows={args.rows:,} baseline_rss={base:.0f} MB", flush=True)

    t0 = time.perf_counter()
    res = dedupe_to_parquet(pq_path, out_dir=out_dir, config=cfg)
    wall = time.perf_counter() - t0
    sampler.stop()

    print(
        f"[bench] RESULT mode={args.mode} rows={args.rows} status=completed "
        f"wall={wall:.1f} peak_mb={sampler.peak_mb:.0f} "
        f"streaming={res.get('streaming')} "
        f"unique={res.get('unique_count')} dupes={res.get('dupes_count')} "
        f"golden={res.get('golden_count')} pairs={res.get('pairs')}",
        flush=True,
    )


if __name__ == "__main__":
    main()
