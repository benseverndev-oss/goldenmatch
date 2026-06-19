#!/usr/bin/env python
"""Synthetic MinHash/LSH recall benchmark (#1081).

Generates a corpus of seed documents plus near-duplicate variants (controlled
insert/delete/substitute edits) with a known duplicate-pair set, then measures:

- **recall**: fraction of true near-dup pairs that share >= 1 LSH bucket
- **reduction**: 1 - candidate_pairs / all_pairs (how much work LSH saves)

``measure_recall`` is importable (the always-on CI gate calls it); ``main`` runs
a small sweep and prints a table. Deterministic given a fixed seed.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from goldenmatch.config.schemas import LSHKeyConfig  # noqa: E402
from goldenmatch.core.lsh_blocker import MinHashLSHBlocker  # noqa: E402


def _corrupt(tokens: list[str], rate: float, rng: random.Random) -> list[str]:
    """Apply per-token delete / substitute / insert edits at ~``rate``."""
    out: list[str] = []
    for t in tokens:
        rv = rng.random()
        if rv < rate / 3:
            continue  # delete
        elif rv < 2 * rate / 3:
            out.append(str(rng.randint(0, 9999)))  # substitute
        else:
            out.append(t)
        if rng.random() < rate / 3:
            out.append(str(rng.randint(0, 9999)))  # insert
    return out


def generate_corpus(
    num_seed: int, variants: int, edit_rate: float, seed: int
) -> tuple[list[str], set[tuple[int, int]]]:
    """Seed docs + ``variants`` near-dup copies each. Returns (docs, true pairs)."""
    rng = random.Random(seed)
    docs: list[list[str]] = []
    truth: set[tuple[int, int]] = set()
    for _ in range(num_seed):
        base = [str(rng.randint(0, 9999)) for _ in range(rng.randint(20, 40))]
        gid = len(docs)
        docs.append(base)
        for _ in range(variants):
            docs.append(_corrupt(base, edit_rate, rng))
        for off in range(1, variants + 1):
            truth.add((gid, gid + off))
    return [" ".join(d) for d in docs], truth


def measure_recall(
    num_seed: int = 60,
    variants: int = 3,
    edit_rate: float = 0.1,
    mode: str = "word",
    k: int = 2,
    num_perms: int = 128,
    threshold: float = 0.5,
    seed: int = 0,
) -> dict:
    """Generate a synthetic corpus and measure LSH recall + reduction."""
    docs, truth = generate_corpus(num_seed, variants, edit_rate, seed)
    blocker = MinHashLSHBlocker.from_config(
        LSHKeyConfig(column="t", mode=mode, k=k, num_perms=num_perms, threshold=threshold, seed=seed)
    )
    candidates = blocker.candidate_pairs(docs)
    n = len(docs)
    all_pairs = n * (n - 1) // 2
    found = sum(1 for p in truth if p in candidates)
    return {
        "num_docs": n,
        "num_bands": blocker.num_bands,
        "rows_per_band": num_perms // blocker.num_bands,
        "true_pairs": len(truth),
        "candidate_pairs": len(candidates),
        "recall": found / len(truth) if truth else 1.0,
        "reduction": 1.0 - (len(candidates) / all_pairs) if all_pairs else 1.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--num-seed", type=int, default=60)
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--mode", default="word")
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--num-perms", type=int, default=128)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    print(
        f"{'edit_rate':>9} {'thr':>5} {'recall':>7} {'reduction':>10} "
        f"{'bands':>6} {'docs':>6} {'cands':>8}"
    )
    for edit_rate in (0.1, 0.2, 0.3):
        for threshold in (0.4, 0.5, 0.7):
            m = measure_recall(
                num_seed=args.num_seed,
                variants=args.variants,
                edit_rate=edit_rate,
                mode=args.mode,
                k=args.k,
                num_perms=args.num_perms,
                threshold=threshold,
                seed=args.seed,
            )
            print(
                f"{edit_rate:>9} {threshold:>5} {m['recall']:>7.3f} "
                f"{m['reduction']:>10.4f} {m['num_bands']:>6} {m['num_docs']:>6} "
                f"{m['candidate_pairs']:>8}"
            )


if __name__ == "__main__":
    main()
