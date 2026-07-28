#!/usr/bin/env python3
"""Time Python `jellyfish` soundex/nysiis on the SAME ASCII name corpus the Rust
`bench_vs_jellyfish` example timed, so the goldenphonetic-vs-jellyfish ratios line
up on identical inputs.

Run:
    cargo run --release --example bench_vs_jellyfish   # writes target/bench_ascii_names.txt
    uv run --no-project --with jellyfish --python 3.12 python \
        packages/rust/extensions/goldenphonetic-core/scripts/bench_jellyfish.py

Reports ns/call and ms/corpus-pass for each encoder (ms/corpus-pass is directly
comparable to the Rust example's output).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import jellyfish


def bench(fn, corpus, reps):
    # warm up
    for s in corpus:
        fn(s)
    t0 = time.perf_counter()
    acc = 0
    for _ in range(reps):
        for s in corpus:
            acc += len(fn(s))
    dt = time.perf_counter() - t0
    _ = acc
    calls = reps * len(corpus)
    return dt / calls * 1e9  # ns/call


def main() -> int:
    corpus_path = (
        Path(__file__).resolve().parent.parent / "target" / "bench_ascii_names.txt"
    )
    if len(sys.argv) > 1:
        corpus_path = Path(sys.argv[1])
    if not corpus_path.exists():
        print(f"corpus not found: {corpus_path}", file=sys.stderr)
        print("run `cargo run --release --example bench_vs_jellyfish` first", file=sys.stderr)
        return 1

    corpus = corpus_path.read_text(encoding="utf-8").split("\n")
    reps = 20

    sx = bench(jellyfish.soundex, corpus, reps)
    ny = bench(jellyfish.nysiis, corpus, reps)

    def total_ms(ns):
        return ns * len(corpus) / 1e6

    print(f"jellyfish ASCII name corpus: {len(corpus)} names x {reps} reps")
    print()
    print(f"{'encoder':<10} {'ns/call':>12} {'ms / corpus-pass':>16}")
    print("-" * 40)
    print(f"{'soundex':<10} {sx:>10.1f}ns {total_ms(sx):>13.1f}ms")
    print(f"{'nysiis':<10} {ny:>10.1f}ns {total_ms(ny):>13.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
