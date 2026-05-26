#!/usr/bin/env python3
"""Benchmark the in-house ER embedder's pairwise discrimination.

Trains a `goldenmatch.embeddings.inhouse` model on labeled pairs and reports the
best pairwise F1 (cosine-threshold sweep) against a no-signal floor — the
methodology for the #506 "F1 vs Vertex/none" comparison.

Modes:
  --synthetic N   self-contained: generate N corrupted-name pairs, no datasets.
  --pairs FILE    CSV with columns text_a,text_b,label (1=match, 0=non-match).

Real benchmark datasets (DBLP-ACM / Febrl / NCVR) live under the gitignored
tests/benchmarks/datasets/; point --pairs at a labeled-pair export from those.
"""
from __future__ import annotations

import argparse
import random
import sys

import numpy as np
from goldenmatch.embeddings.inhouse import FeaturizerConfig, TrainConfig, train_embedder


def _synthetic_pairs(n: int, seed: int = 0) -> list[tuple[str, str, int]]:
    rng = random.Random(seed)
    first = ["John", "Jane", "Robert", "Margaret", "William", "Elizabeth", "Michael",
             "Patricia", "David", "Jennifer", "Acme", "Globex", "Initech", "Umbrella"]
    last = ["Smith", "Jones", "Chen", "Warren", "Gates", "Brown", "Corporation",
            "Industries", "Holdings", "Systems", "Partners", "Group"]

    def corrupt(s: str) -> str:
        cs = list(s)
        op = rng.random()
        i = rng.randrange(len(cs))
        if op < 0.4 and len(cs) > 2:  # delete
            del cs[i]
        elif op < 0.7:  # substitute
            cs[i] = rng.choice("abcdefghijklmnopqrstuvwxyz")
        elif op < 0.85 and i < len(cs) - 1:  # transpose
            cs[i], cs[i + 1] = cs[i + 1], cs[i]
        else:  # insert
            cs.insert(i, rng.choice("abcdefghijklmnopqrstuvwxyz"))
        return "".join(cs)

    names = [f"{rng.choice(first)} {rng.choice(last)}" for _ in range(n)]
    pairs: list[tuple[str, str, int]] = []
    for nm in names:
        pairs.append((nm, corrupt(nm), 1))           # positive: a typo'd variant
        other = rng.choice(names)
        pairs.append((nm, other, 1 if other == nm else 0))  # likely negative
    return pairs


def _load_pairs(path: str) -> list[tuple[str, str, int]]:
    import polars as pl

    df = pl.read_csv(path, encoding="utf8-lossy", ignore_errors=True)
    return [(str(a), str(b), int(y))
            for a, b, y in zip(df["text_a"], df["text_b"], df["label"])]


def _best_f1(scores: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    best_f1, best_t = 0.0, 0.0
    for t in np.linspace(-0.2, 1.0, 61):
        pred = scores >= t
        tp = int(np.sum(pred & (labels == 1)))
        fp = int(np.sum(pred & (labels == 0)))
        fn = int(np.sum(~pred & (labels == 1)))
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_f1, best_t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", type=int, metavar="N")
    ap.add_argument("--pairs", type=str, metavar="FILE")
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--n-features", type=int, default=4096)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    if args.synthetic:
        pairs = _synthetic_pairs(args.synthetic, seed=args.seed)
    elif args.pairs:
        pairs = _load_pairs(args.pairs)
    else:
        ap.error("pass --synthetic N or --pairs FILE")

    labels = np.array([y for _a, _b, y in pairs])
    pos_rate = float(labels.mean())

    model, report = train_embedder(
        pairs,
        TrainConfig(dim=args.dim, epochs=args.epochs, seed=args.seed,
                    featurizer=FeaturizerConfig(n_features=args.n_features)),
    )
    ea = model.embed(["" if a is None else a for a, _b, _y in pairs])
    eb = model.embed(["" if b is None else b for _a, b, _y in pairs])
    cos = np.sum(ea * eb, axis=1)
    f1, thr = _best_f1(cos, labels)

    # No-signal floor: best F1 achievable with zero discrimination (predict-all).
    floor = 2 * pos_rate / (pos_rate + 1.0)

    print(f"pairs={len(pairs)}  positives={pos_rate:.1%}  dim={args.dim} "
          f"n_features={args.n_features} epochs={args.epochs}")
    print(f"train separation: {report.separation_before:.3f} -> {report.separation_after:.3f}")
    print(f"in-house  best pairwise F1 = {f1:.3f}  @cos>={thr:.2f}")
    print(f"no-signal floor (predict-all) F1 = {floor:.3f}")
    print(f"lift vs floor: {f1 - floor:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
