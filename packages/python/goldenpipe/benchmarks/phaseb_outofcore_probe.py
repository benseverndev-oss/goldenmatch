#!/usr/bin/env python3
"""Phase B baseline: is out-of-core streaming the right target for the ER pipeline?

The relocatable-stage-contract's Phase B is an out-of-core streaming ``Frame``.
Per the design gate, it must first be shown that holding the whole frame is the
bottleneck. This probe answers that with two measurements, on a FRESH PROCESS per
size (``ru_maxrss`` is a process-lifetime peak, so per-size isolation is required
for a clean number):

  1. **Peak RSS vs input-frame size.** If peak process memory is a small multiple
     of the input frame, streaming the frame could help. If peak RSS dwarfs the
     frame, the memory bottleneck is a *stage's internal structures* (dedupe's
     candidate / scored pairs), and streaming the input frame cannot relieve it.
  2. **Which stage dominates, and can it stream?** ``goldenmatch.dedupe`` needs
     ALL records to find cross-record duplicates -- it is inherently whole-dataset
     and cannot process a stream batch-by-batch.

Run one size (fresh process):
    python benchmarks/phaseb_outofcore_probe.py --rows 100000
"""
from __future__ import annotations

import argparse
import os
import random
import resource
import tempfile

import polars as pl


def _peak_rss_mb() -> float:
    # ru_maxrss is KB on Linux, bytes on macOS. Assume Linux (CI) -> KB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _make(path: str, rows: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    first = ["Jon", "John", "Mary", "Bob", "Robert", "Sue"]
    last = ["Smith", "Smyth", "Jones", "Lee", "Kim", "Patel"]
    recs = []
    for i in range(rows):
        f, ln = rng.choice(first), rng.choice(last)
        e = f"{f.lower()}.{ln.lower()}{rng.randint(0, 99)}@x.com"
        recs.append({"id": i, "first": f, "last": ln, "email": e,
                     "city": rng.choice(["NYC", "LA", "Chicago"]), "amt": round(rng.random() * 1000, 2)})
        if rng.random() < 0.2:  # ~20% near-duplicate rows
            recs.append({"id": rows + i, "first": f, "last": ln, "email": f"  {e.upper()} ",
                         "city": "NYC", "amt": round(rng.random() * 1000, 2)})
    pl.DataFrame(recs).write_csv(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=100_000)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "e.csv")
    _make(path, args.rows)
    frame_mb = pl.read_csv(path, ignore_errors=True, encoding="utf8-lossy").estimated_size() / 1e6

    from goldenpipe import Pipeline

    res = Pipeline().run(source=path)
    peak = _peak_rss_mb()
    dedupe_ms = (res.timing or {}).get("goldenmatch.dedupe", 0.0) * 1000
    total_ms = sum((res.timing or {}).values()) * 1000
    print(
        f"rows={args.rows:>7}  frame={frame_mb:6.2f} MB  peak_RSS={peak:7.0f} MB  "
        f"peak/frame={peak / max(frame_mb, 0.01):6.1f}x  "
        f"dedupe={dedupe_ms:6.0f} ms ({dedupe_ms / max(total_ms, 1) * 100:4.1f}% of stages)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
