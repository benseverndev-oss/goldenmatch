"""Bench harness for the cluster orchestration kernel prototype.

Spec: docs/superpowers/specs/2026-05-30-cluster-orchestration-kernel-spec.md

Measures the Python build_clusters loop vs the native build_clusters_native
kernel on a synthetic shape that mirrors the QIS 10M bench's cluster wall
(2M clusters x ~5 members each, ~20M scored pairs).

Run locally once the native module is built:
    .venv/Scripts/python.exe scripts/bench_native_cluster_kernel.py

Skipped when the native module isn't available -- there's nothing to compare.
"""
from __future__ import annotations

import random
import time

try:
    import goldenmatch._native as native_mod  # type: ignore[import-not-found]
except ImportError:
    raise SystemExit("goldenmatch._native not built; run scripts/build_native.py")  # noqa: B904

if not hasattr(native_mod, "build_clusters_native"):
    raise SystemExit(
        "native module loaded but build_clusters_native not exposed -- rebuild"
    )

from goldenmatch.core.cluster import build_clusters as py_build_clusters


def gen_workload(n_records: int, n_pairs: int, seed: int = 42):
    """Generate a synthetic (pairs, all_ids) tuple that produces ~n_records/5
    multi-record clusters at full pair count."""
    rng = random.Random(seed)
    all_ids = list(range(n_records))
    pairs: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    while len(pairs) < n_pairs:
        a = rng.randint(0, n_records - 1)
        b = rng.randint(0, n_records - 1)
        if a == b:
            continue
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((key[0], key[1], rng.random() * 0.4 + 0.6))
    return pairs, all_ids


def time_python(pairs, all_ids):
    t0 = time.perf_counter()
    result = py_build_clusters(
        pairs, all_ids, max_cluster_size=1000, auto_split=False,
    )
    return time.perf_counter() - t0, result


def time_native(pairs, all_ids):
    t0 = time.perf_counter()
    result = native_mod.build_clusters_native(pairs, all_ids, 1000)
    return time.perf_counter() - t0, result


def main():
    # Three shapes: small (smoke test), medium (interactive run), large (closer
    # to QIS 10M bucket-realistic shape). Scale down the largest if running on
    # a memory-constrained box.
    shapes = [
        ("smoke",  10_000,    50_000),
        ("medium", 100_000,   500_000),
        ("large",  500_000, 2_000_000),
    ]
    print(f"{'shape':>8} {'records':>10} {'pairs':>10} "
          f"{'py s':>8} {'rs s':>8} {'speedup':>8}")
    for label, n_records, n_pairs in shapes:
        pairs, all_ids = gen_workload(n_records, n_pairs)
        py_s, py_result = time_python(pairs, all_ids)
        rs_s, rs_result = time_native(pairs, all_ids)
        speedup = py_s / rs_s if rs_s > 0 else float("inf")
        print(f"{label:>8} {n_records:>10,} {n_pairs:>10,} "
              f"{py_s:>8.2f} {rs_s:>8.2f} {speedup:>7.2f}x")
        # Sanity: both paths should produce the same number of clusters.
        if len(py_result) != len(rs_result):
            print(f"  WARNING: cluster count diverges -- py={len(py_result)} "
                  f"rs={len(rs_result)}")


if __name__ == "__main__":
    main()
