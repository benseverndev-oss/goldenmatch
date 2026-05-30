"""Bench harness for the bulk record fingerprint kernel prototype.

Spec: docs/superpowers/specs/2026-05-30-bulk-record-fingerprint-kernel-spec.md

Compares the per-record Python loop (single-record `record_fingerprint`
called N times) vs the bulk `record_fingerprints_batch` kernel on three
shapes that mirror identity-resolve hot loops.

Decision gate (large shape, 1M records): >= 2x speedup ships the wire-up
PR. Below that the per-record Python pre-pass is the floor.

Run locally once the native module is built:
    .venv/Scripts/python.exe scripts/bench_native_bulk_fingerprint.py
"""
from __future__ import annotations

import random
import time

try:
    import goldenmatch._native as native_mod  # type: ignore[import-not-found]
except ImportError:
    raise SystemExit("goldenmatch._native not built; run scripts/build_native.py")  # noqa: B904

if not hasattr(native_mod, "record_fingerprints_batch"):
    raise SystemExit(
        "native module loaded but record_fingerprints_batch not exposed -- rebuild"
    )

from goldenmatch.core._hashing import record_fingerprint


def gen_records(n: int, seed: int = 42) -> list[dict]:
    """Generate N realistic identity-shaped records: first/last name + zip +
    email + birth_year. Five fields per record matches the QIS shape."""
    rng = random.Random(seed)
    first_names = ["alice", "bob", "carol", "david", "eve", "frank", "grace", "heidi"]
    last_names = ["smith", "jones", "doe", "brown", "wilson", "moore"]
    records = []
    for i in range(n):
        records.append({
            "first_name": rng.choice(first_names),
            "last_name": rng.choice(last_names),
            "zip": str(10000 + rng.randint(0, 89999)),
            "email": f"user{i}@example.com",
            "birth_year": 1950 + rng.randint(0, 60),
        })
    return records


def time_python_loop(records: list[dict]) -> tuple[float, list[str]]:
    """Per-record single-call loop -- what identity/resolve.py does today."""
    t0 = time.perf_counter()
    result = [record_fingerprint(r) for r in records]
    return time.perf_counter() - t0, result


def time_bulk(records: list[dict]) -> tuple[float, list[str]]:
    """Bulk kernel: one FFI hop, rayon-parallel inside."""
    t0 = time.perf_counter()
    result = native_mod.record_fingerprints_batch(records)
    return time.perf_counter() - t0, result


def main() -> None:
    # Three shapes: smoke for sanity, medium for interactive runs, large
    # for the decision gate. 1M records is the realistic identity-resolve
    # hot-loop scale (QIS realistic = ~2M but pre-PK split usually halves it).
    shapes = [
        ("smoke",   10_000),
        ("medium", 100_000),
        ("large", 1_000_000),
    ]
    print(f"{'shape':>8} {'records':>10} {'py s':>8} {'rs s':>8} {'speedup':>8}")
    for label, n in shapes:
        records = gen_records(n)
        py_s, py_out = time_python_loop(records)
        rs_s, rs_out = time_bulk(records)
        speedup = py_s / rs_s if rs_s > 0 else float("inf")
        print(f"{label:>8} {n:>10,} {py_s:>8.2f} {rs_s:>8.2f} {speedup:>7.2f}x")
        # Sanity: each (py, rs) pair must agree -- the bulk kernel is supposed
        # to be byte-equivalent to the per-record loop.
        if py_out != rs_out:
            mismatches = sum(1 for a, b in zip(py_out, rs_out) if a != b)
            print(f"  WARNING: {mismatches} bytes diverge -- parity broken")


if __name__ == "__main__":
    main()
