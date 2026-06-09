"""Bench harness for the CLK bloom-filter kernel (bloom_clk_batch).

Compares the per-row Python CLK loop (what pprl.compute_bloom_filters did
before the wire-up) vs the bulk native kernel across the three security
presets. The :paranoid preset (trigram x 40 hashes x 2048 bits) is the
heaviest -- the most likely place the kernel earns its keep.

Decision gate (large shape, paranoid): a measured wall-clock speedup that
clears the "second symbol in the wheel" cost before "pprl_bloom" is added to
_GATED_ON. Per the repo perf-audit lesson, treat the lift as a hypothesis
until this prints on a real box.

Run locally once the native module is built:
    .venv/bin/python scripts/bench_native_bloom.py
"""
from __future__ import annotations

import random
import time

try:
    import goldenmatch._native as native_mod  # type: ignore[import-not-found]
except ImportError:
    raise SystemExit("goldenmatch._native not built; run scripts/build_native.py")  # noqa: B904

if not hasattr(native_mod, "bloom_clk_batch"):
    raise SystemExit("native module loaded but bloom_clk_batch not exposed -- rebuild")

from goldenmatch.utils.transforms import (
    _clk_from_prepared,
    _parse_bloom_params,
    _prepare_bloom_input,
)


def gen_values(n: int, seed: int = 42) -> list[str]:
    """Person-like name+token strings -- the PPRL CLK input shape."""
    rng = random.Random(seed)
    first = ["alice", "bob", "carol", "david", "eve", "frank", "grace", "heidi"]
    last = ["smith", "jones", "doe", "brown", "wilson", "moore", "obrien", "mcdonald"]
    return [f"{rng.choice(first)} {rng.choice(last)} {10000 + rng.randint(0, 89999)}" for _ in range(n)]


def time_python(values: list[str], transform: str) -> tuple[float, list[str]]:
    ng, k, sz, key, bal = _parse_bloom_params(transform)
    t0 = time.perf_counter()
    out = [_clk_from_prepared(_prepare_bloom_input(v, ng, bal), ng, k, sz, key) for v in values]
    return time.perf_counter() - t0, out


def time_native(values: list[str], transform: str) -> tuple[float, list[str]]:
    ng, k, sz, key, bal = _parse_bloom_params(transform)
    prepared = [_prepare_bloom_input(v, ng, bal) for v in values]  # preprocessing stays Python
    t0 = time.perf_counter()
    out = native_mod.bloom_clk_batch(prepared, ng, k, sz, key)
    return time.perf_counter() - t0, out


def main() -> None:
    shapes = [("smoke", 5_000), ("medium", 50_000), ("large", 250_000)]
    transforms = ["bloom_filter:standard", "bloom_filter:high", "bloom_filter:paranoid"]
    print(f"{'preset':>22} {'rows':>9} {'py s':>8} {'rs s':>8} {'speedup':>8}")
    for transform in transforms:
        for label, n in shapes:
            values = gen_values(n)
            py_s, py_out = time_python(values, transform)
            rs_s, rs_out = time_native(values, transform)
            speedup = py_s / rs_s if rs_s > 0 else float("inf")
            print(f"{transform + '/' + label:>22} {n:>9,} {py_s:>8.3f} {rs_s:>8.3f} {speedup:>7.2f}x")
            if py_out != rs_out:
                mismatches = sum(1 for a, b in zip(py_out, rs_out) if a != b)
                print(f"  WARNING: {mismatches} CLKs diverge -- parity broken")


if __name__ == "__main__":
    main()
