"""Side-by-side throughput: Python GoldenEmbedModel.embed vs the Rust
``goldenembed bench`` CLI, on identical synthetic text. Prints rows/sec per side
plus a cosine parity spot-check. Reports the ratio (env-dependent); does not
assert (the issue's acceptance target is "Rust beats the pyo3 path on large
batches", but the absolute numbers are machine-dependent).

Usage:
    python scripts/bench_goldenembed.py --model <dir> [--rows N] [--batch B] \
        [--rust-bin <path-to-goldenembed>]

The synthetic corpus matches the Rust ``run_bench`` corpus exactly
(``record number {i} acme corp``) so both sides embed the same text.
"""
from __future__ import annotations

import argparse
import subprocess
import time

import numpy as np

from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel


def synthetic(rows: int) -> list[str]:
    return [f"record number {i} acme corp" for i in range(rows)]


def py_throughput(model_dir: str, rows: int, batch: int, backend: str) -> float:
    m = GoldenEmbedModel.load(model_dir)
    texts = synthetic(rows)
    start = time.perf_counter()
    for i in range(0, rows, batch):
        m.embed(texts[i : i + batch], backend=backend)
    return rows / (time.perf_counter() - start)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--rows", type=int, default=50_000)
    ap.add_argument("--batch", type=int, default=1024)
    ap.add_argument("--rust-bin", default="goldenembed")
    args = ap.parse_args()

    for backend in ("auto", "onnx"):
        rps = py_throughput(args.model, args.rows, args.batch, backend)
        print(f"python[{backend}] rows/sec={rps:,.0f}")

    print("--- rust ---")
    subprocess.run(
        [
            args.rust_bin,
            "bench",
            "--model",
            args.model,
            "--rows",
            str(args.rows),
            "--batch",
            str(args.batch),
        ],
        check=True,
    )

    # Parity spot-check on a small sample: the auto/onnx backends should agree.
    m = GoldenEmbedModel.load(args.model)
    sample = synthetic(8)
    v = m.embed(sample, backend="auto")
    v2 = m.embed(sample, backend="onnx")
    cos = (v * v2).sum(1) / (
        np.linalg.norm(v, axis=1) * np.linalg.norm(v2, axis=1) + 1e-9
    )
    print(f"parity cosine min={cos.min():.6f} (expect ~1.0)")


if __name__ == "__main__":
    main()
