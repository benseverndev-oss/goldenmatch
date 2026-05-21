"""Generate a chain-heavy graph for Phase 5.5 adversarial WCC bench.

Chains are label-propagation's worst case (per Sem Sinchenko, GraphFrames
maintainer): each iteration only propagates labels one hop down the chain,
so convergence is O(chain_length).

Output parquet schema: {id_a:int64, id_b:int64, score:float64}.

Run:
    python scripts/generate_chain_dataset.py \
        --chains 5000000 --chain-length 10 \
        --output chains_50m.parquet
"""

from __future__ import annotations

import argparse
import sys
import time

import polars as pl


def generate_chain_graph(n_chains: int, chain_length: int) -> pl.DataFrame:
    """One chain per `chain_idx`: nodes [base, base+1, ..., base+L-1]
    with edges (base+i, base+i+1) for i in [0, L-1).
    """
    ids_a, ids_b, scores = [], [], []
    for chain_idx in range(n_chains):
        base = chain_idx * chain_length
        for i in range(chain_length - 1):
            ids_a.append(base + i)
            ids_b.append(base + i + 1)
            scores.append(1.0)
    return pl.DataFrame({
        "id_a": ids_a,
        "id_b": ids_b,
        "score": scores,
    })


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chains", type=int, default=5_000_000)
    ap.add_argument("--chain-length", type=int, default=10)
    ap.add_argument("--output", type=str, required=True)
    args = ap.parse_args()

    t0 = time.perf_counter()
    df = generate_chain_graph(args.chains, args.chain_length)
    print(f"generated {df.height} edges across {args.chains} chains in {time.perf_counter() - t0:.1f}s")

    t1 = time.perf_counter()
    df.write_parquet(args.output)
    print(f"wrote {args.output} in {time.perf_counter() - t1:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
