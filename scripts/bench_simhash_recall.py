#!/usr/bin/env python
"""Synthetic SimHash/LSH recall benchmark (#1082).

Generates a corpus of seed Gaussian vectors plus near-duplicate variants
(per-dim Gaussian jitter) with a known duplicate-pair set, then measures:

- **recall**: fraction of true near-dup pairs that share >= 1 SimHash LSH bucket
- **reduction**: 1 - candidate_pairs / all_pairs (how much work LSH saves)

This is the *semantic* analogue of ``bench_lsh_recall.py`` (lexical MinHash):
SimHash buckets dense vectors by cosine direction, so near-dup vectors (small
jitter -> high cosine similarity) collide in a band.

``measure_simhash_recall`` is importable (the always-on CI gate calls it);
``main`` runs a small sweep and prints a table. Deterministic given a fixed seed.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

_PKG = Path(__file__).resolve().parents[1] / "packages" / "python" / "goldenmatch"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from goldenmatch.config.schemas import SimHashKeyConfig  # noqa: E402
from goldenmatch.core.simhash_blocker import SimHashLSHBlocker  # noqa: E402


def generate_corpus(
    num_seed: int, variants: int, noise: float, dim: int, seed: int
) -> tuple[list[list[float]], set[tuple[int, int]]]:
    """Seed Gaussian vectors + ``variants`` near-dup copies each.

    Each variant is ``base + per-dim gauss(0, noise)``, so small ``noise`` keeps
    the variant cosine-near its base. Returns (vectors, true near-dup pairs).
    """
    rng = random.Random(seed)
    vectors: list[list[float]] = []
    truth: set[tuple[int, int]] = set()
    for _ in range(num_seed):
        base = [rng.gauss(0.0, 1.0) for _ in range(dim)]
        gid = len(vectors)
        vectors.append(base)
        for _ in range(variants):
            vectors.append([x + rng.gauss(0.0, noise) for x in base])
        for off in range(1, variants + 1):
            truth.add((gid, gid + off))
    return vectors, truth


def measure_simhash_recall(
    num_seed: int = 60,
    variants: int = 3,
    noise: float = 0.3,
    dim: int = 64,
    num_planes: int = 256,
    num_bands: int = 32,
    seed: int = 1,
) -> dict:
    """Generate a synthetic vector corpus and measure SimHash recall + reduction."""
    vectors, truth = generate_corpus(num_seed, variants, noise, dim, seed)
    blocker = SimHashLSHBlocker.from_config(
        SimHashKeyConfig(column="v", num_planes=num_planes, num_bands=num_bands, seed=13)
    )
    embeddings = np.asarray(vectors, dtype=np.float64)
    candidates = blocker.candidate_pairs(embeddings)
    n = len(vectors)
    all_pairs = n * (n - 1) // 2
    found = sum(1 for p in truth if p in candidates)
    return {
        "num_vectors": n,
        "num_bands": blocker.num_bands,
        "rows_per_band": num_planes // blocker.num_bands,
        "true_pairs": len(truth),
        "candidate_pairs": len(candidates),
        "recall": found / len(truth) if truth else 1.0,
        "reduction": 1.0 - (len(candidates) / all_pairs) if all_pairs else 1.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--num-seed", type=int, default=60)
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--num-planes", type=int, default=256)
    ap.add_argument("--num-bands", type=int, default=32)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    print(
        f"{'noise':>6} {'recall':>7} {'reduction':>10} "
        f"{'bands':>6} {'vecs':>6} {'cands':>8}"
    )
    for noise in (0.1, 0.3, 0.6, 0.9):
        m = measure_simhash_recall(
            num_seed=args.num_seed,
            variants=args.variants,
            noise=noise,
            dim=args.dim,
            num_planes=args.num_planes,
            num_bands=args.num_bands,
            seed=args.seed,
        )
        print(
            f"{noise:>6} {m['recall']:>7.3f} {m['reduction']:>10.4f} "
            f"{m['num_bands']:>6} {m['num_vectors']:>6} {m['candidate_pairs']:>8}"
        )


if __name__ == "__main__":
    main()
