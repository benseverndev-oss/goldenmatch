"""Profile the out-of-core STREAMING FS path's resident-memory peak.

Generates N synthetic person rows, builds an EXPLICIT probabilistic config
(avoids the degenerate zero-config `__placeholder__` exact matchkey on synthetic
data), runs `dedupe_to_parquet` with `GOLDENMATCH_FS_OUT_OF_CORE=1`, and samples
VmRSS on a background thread to report the TRUE peak + when it occurs relative to
the stage log — so we can see whether the binding peak is the prepared frame held
resident through clustering/output, the pair stream, or a spill transient.

Usage: python scripts/bench_fs_streaming_peak.py [N_ROWS]
"""
from __future__ import annotations

import os
import sys
import threading
import time

os.environ.setdefault("GOLDENMATCH_FS_OUT_OF_CORE", "1")
os.environ.setdefault("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
os.environ.setdefault("GOLDENMATCH_FS_OOC_DEBUG", "1")
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")


def _vmrss_kb() -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return 0


class RssSampler(threading.Thread):
    def __init__(self, interval: float = 0.05):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_kb = 0
        self.peak_t = 0.0
        self._stopev = threading.Event()
        self.t0 = time.perf_counter()

    def run(self):
        last_log = 0.0
        while not self._stopev.is_set():
            rss = _vmrss_kb()
            t = time.perf_counter() - self.t0
            if rss > self.peak_kb:
                self.peak_kb = rss
                self.peak_t = t
            if t - last_log >= 2.0:
                print(f"[rss] t={t:5.1f}s  {rss/1024:7.0f} MB", flush=True)
                last_log = t
            self._stopev.wait(self.interval)

    def stop(self):
        self._stopev.set()
        self.join(timeout=2)


def _write_person_csv(n: int, path: str) -> None:
    import csv as _csv
    import random

    random.seed(1234)
    firsts = ["John", "Jon", "Jane", "Janet", "Bob", "Robert", "Alice", "Alicia",
              "Tom", "Thomas", "Amy", "Amie", "Mary", "Maria", "Bill", "William"]
    lasts = ["Smith", "Smyth", "Doe", "Jones", "Brown", "Wilson", "Clark",
             "Davis", "Miller", "Garcia", "Martinez", "Lee", "Walker", "Hall"]
    # ~40% of rows are a near-dup of a prior row (same zip, perturbed name) so
    # blocks form and pairs actually score -- a realistic dedupe shape. Keep a
    # bounded ring buffer of recent (last, zip) so generation is O(N), streamed
    # straight to the CSV (no in-driver frame).
    # Filler columns so the prepared frame is representative of real person data
    # (~14 cols), not a 3-col toy -- exposes the held-frame residency cost.
    n_filler = int(os.environ.get("BENCH_FILLER_COLS", "11"))
    filler_cols = [f"attr_{k}" for k in range(n_filler)]
    ring: list[tuple[str, str]] = []
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["first_name", "last_name", "zip", *filler_cols])
        for i in range(n):
            if ring and random.random() < 0.4:
                last, zp = random.choice(ring)
                fn = random.choice(firsts)
            else:
                fn = random.choice(firsts)
                last = random.choice(lasts)
                zp = f"{random.randint(10000, 99999):05d}"
            filler = [f"val_{random.randint(0, 999999):06d}" for _ in filler_cols]
            w.writerow([fn, last, zp, *filler])
            ring.append((last, zp))
            if len(ring) > 50:
                ring.pop(0)


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
    # Realistic bounded-block blocking: zip only (many small blocks, like real
    # person data). last_name (14 distinct values) makes pathological mega-blocks.
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["zip"])],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking, backend="bucket")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    import tempfile

    print(f"[bench] generating {n:,} person rows ...", flush=True)
    tmpd = tempfile.mkdtemp(prefix="gm_fs_peak_")
    csv_path = os.path.join(tmpd, "people.csv")
    _write_person_csv(n, csv_path)

    from goldenmatch import dedupe_to_parquet

    cfg = _fs_config()
    out_dir = os.path.join(tmpd, "out")

    sampler = RssSampler()
    sampler.start()
    base_rss = _vmrss_kb()
    print(f"[bench] baseline VmRSS {base_rss/1024:.0f} MB", flush=True)

    t0 = time.perf_counter()
    res = dedupe_to_parquet(csv_path, out_dir=out_dir, config=cfg)
    wall = time.perf_counter() - t0
    sampler.stop()

    print(f"\n[bench] streaming={res.get('streaming')} wall={wall:.1f}s", flush=True)
    print(f"[bench] unique={res.get('unique_count')} dupes={res.get('dupes_count')} "
          f"golden={res.get('golden_count')} pairs={res.get('pairs')}", flush=True)
    print(f"[bench] PEAK VmRSS {sampler.peak_kb/1024:.0f} MB "
          f"(at t={sampler.peak_t:.1f}s of {wall:.1f}s)", flush=True)
    print(f"[bench] peak-over-baseline {(sampler.peak_kb-base_rss)/1024:.0f} MB", flush=True)


if __name__ == "__main__":
    main()
