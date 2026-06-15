#!/usr/bin/env python3
"""Wall-clock bench: pure-Python levenshtein vs the native-kernel levenshtein.

Deliverable 3 of the single-kernel-collapse spike. Answers kill-criterion item
(4): "measured wall shows the kernel path isn't at least neutral vs pure on real
workloads." This is the audit lesson made executable — MEASURE wall-clock with the
workload of interest (5-run median wall on real name-shaped pairs), never assume.

STANDALONE: imports nothing from a default path. It calls each implementation in a
tight per-pair loop over a realistic corpus of name-shaped string pairs and reports
records/sec + the kernel/pure ratio.

  pure   = rapidfuzz `Levenshtein.normalized_similarity` (what `score_field`
           dispatches to for the levenshtein scorer — the EXISTING default).
  kernel = `goldenmatch._native.levenshtein_similarity` (the score-core PyO3
           binding the collapse would standardize on).

NOTE this is the PER-PAIR boundary cost (one PyO3 / Python call per pair), the
pessimal shape for the kernel — the production hot path batches NxN per block
(`score_field_matrix` / `score_block_pairs`), amortizing the boundary. A per-pair
bench that comes out neutral-or-better is therefore a conservative lower bound on
the kernel's real advantage; a per-pair bench that comes out WORSE would still be
fine for the collapse (the batch path is what ships) but is reported honestly.

If the kernel is unbuildable in this env the kernel leg is SKIPPED and the script
reports the bench design + that numbers are pending CI / a built wheel.

Usage:
    python scripts/bench_kernel_levenshtein.py            # 4000 pairs, 5 runs
    python scripts/bench_kernel_levenshtein.py --pairs 20000 --runs 5
    python scripts/bench_kernel_levenshtein.py --assert-not-slower  # CI gate

The optional ``--assert-not-slower`` flag turns the bench into a hard gate (the
R1 wheel CI lane uses it): it exits 1 if the native kernel is more than a small
tolerance slower than pure (the kill-criterion-(4) "at least neutral" contract),
and — paired with ``--require-kernel`` — also exits 1 if the kernel is absent so
the just-built wheel is actually exercised. Default behavior is unchanged: with
neither flag the bench reports numbers and exits 0 (skip-on-absent preserved), so
the spike's standalone-report use stays intact.
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "packages" / "python" / "goldenmatch"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))


def _load_pure():
    from rapidfuzz.distance import Levenshtein

    return Levenshtein.normalized_similarity


def _load_kernel():
    try:
        import goldenmatch._native as native  # type: ignore[import-not-found]

        return native.levenshtein_similarity, None
    except Exception as exc_intree:  # noqa: BLE001
        try:
            from goldenmatch_native import _native as native  # type: ignore[import-not-found]

            return native.levenshtein_similarity, None
        except Exception as exc_wheel:  # noqa: BLE001
            return None, (
                f"in-tree ({type(exc_intree).__name__}: {exc_intree}); "
                f"wheel ({type(exc_wheel).__name__}: {exc_wheel})"
            )


# A small pool of realistic, ER-shaped surnames + corrupted variants so the
# levenshtein distances span the meaningful 0.5-1.0 range (where match decisions
# actually live), not random noise that's almost always 0.
_FIRST = ["john", "jane", "michael", "sara", "david", "emily", "robert", "maria"]
_LAST = ["smith", "smyth", "johnson", "jonson", "williams", "willems", "brown",
         "braun", "müller", "muller", "garcia", "garzia", "nguyen", "nguyenn"]


def build_pairs(n: int, seed: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    pairs: list[tuple[str, str]] = []
    for _ in range(n):
        a = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        if rng.random() < 0.6:
            # near-duplicate: 0-2 char edits (the realistic dedupe shape)
            b = list(a)
            for _ in range(rng.randint(0, 2)):
                if b:
                    b[rng.randrange(len(b))] = rng.choice("abcdefghijklmnopqrstuvwxyz")
            b = "".join(b)
        else:
            b = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
        pairs.append((a, b))
    return pairs


def _time_loop(fn, pairs: list[tuple[str, str]]) -> float:
    t0 = time.perf_counter()
    acc = 0.0
    for a, b in pairs:
        acc += fn(a, b)
    dt = time.perf_counter() - t0
    # Touch acc so the loop can't be optimized away.
    if acc < 0:  # pragma: no cover
        raise AssertionError
    return dt


def median_wall(fn, pairs, runs: int) -> float:
    return statistics.median(_time_loop(fn, pairs) for _ in range(runs))


# How much slower than pure the kernel may be before `--assert-not-slower` fails.
# 0.85 = the kernel must be at least 85% of pure's throughput (≤ ~18% slower).
# This is deliberately lenient: the per-pair shape is the kernel's PESSIMAL case
# (one PyO3 boundary per call), and shared-runner wall is noisy; the gate exists
# to catch a #688-class CLIFF (multi-x regression), not single-digit-% jitter.
RATIO_FLOOR = 0.85


def run(pairs_n: int, runs: int, seed: int, require_kernel: bool, assert_not_slower: bool) -> int:
    pairs = build_pairs(pairs_n, seed)
    pure = _load_pure()
    kernel, reason = _load_kernel()

    print(f"bench: levenshtein per-pair  pairs={pairs_n}  runs={runs} (median wall)")
    print("  (per-pair boundary shape — the production hot path batches NxN; see module docstring)")

    # warm-up
    _time_loop(pure, pairs[:64])
    pure_wall = median_wall(pure, pairs, runs)
    pure_rps = pairs_n / pure_wall if pure_wall else float("inf")
    print(f"  pure (rapidfuzz)  : {pure_wall * 1e3:8.2f} ms median   {pure_rps:12,.0f} rec/s")

    if kernel is None:
        print(f"  kernel            : SKIP (kernel unavailable: {reason})")
        if require_kernel:
            print("\nFAIL: --require-kernel set but the native kernel is not importable.")
            return 1
        print("\nKernel numbers PENDING: build with `python scripts/build_native.py` or run in CI.")
        return 0

    _time_loop(kernel, pairs[:64])
    kern_wall = median_wall(kernel, pairs, runs)
    kern_rps = pairs_n / kern_wall if kern_wall else float("inf")
    print(f"  kernel (score-core): {kern_wall * 1e3:8.2f} ms median   {kern_rps:12,.0f} rec/s")

    ratio = pure_wall / kern_wall if kern_wall else float("inf")
    faster = "kernel faster" if ratio > 1 else "pure faster"
    print(f"\n  ratio pure/kernel = {ratio:.2f}x  ({faster})")
    print("  NOTE per-pair is the kernel's pessimal shape (boundary per call); the shipped")
    print("       path batches per block. Kill-criterion (4) asks only for NEUTRAL-OR-BETTER")
    print("       on the real (batched) workload — this per-pair number is a conservative floor.")

    # Machine-greppable line for CI step summaries (mirrors bench_issue_688.py).
    print(f"BENCH_RATIO_PURE_OVER_KERNEL={ratio:.4f}")

    if assert_not_slower and ratio < RATIO_FLOOR:
        print(f"\nFAIL: kernel slower than pure beyond tolerance "
              f"(ratio {ratio:.2f}x < floor {RATIO_FLOOR:.2f}x). "
              "A #688-class regression would land here.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pairs", type=int, default=4000)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--seed", type=int, default=20260614)
    p.add_argument("--require-kernel", action="store_true",
                   help="exit 1 if the native kernel is not importable (CI wheel lane)")
    p.add_argument("--assert-not-slower", action="store_true",
                   help="exit 1 if the kernel is more than a small tolerance slower than pure")
    args = p.parse_args(argv)
    return run(args.pairs, args.runs, args.seed, args.require_kernel, args.assert_not_slower)


if __name__ == "__main__":
    raise SystemExit(main())
