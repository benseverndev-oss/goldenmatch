#!/usr/bin/env python3
"""Benchmark the in-house ER embedder's pairwise discrimination.

Trains a `goldenmatch.embeddings.inhouse` model on labeled pairs and reports
held-out pairwise F1 (cosine-threshold tuned on train) for:
- the **untrained** model (random-projection char-n-gram = lexical baseline),
- the **trained** in-house model,
- the no-signal floor (predict-all),
so the training lift is explicit. Designed to run in CI (the
`bench-inhouse-embedder` workflow) and write Markdown to the step summary.

Modes:
  --febrl3        real ER data via the recordlinkage library (no download).
  --synthetic N   self-contained corrupted-name pairs.
  --pairs FILE    CSV with columns text_a,text_b,label (1=match, 0=non-match).
"""
from __future__ import annotations

import argparse
import random
import sys

import numpy as np
from goldenmatch.embeddings.inhouse import (
    EmbedModelConfig,
    FeaturizerConfig,
    GoldenEmbedModel,
    TrainConfig,
    train_embedder,
)

LabeledPair = tuple[str, str, int]


def _synthetic_pairs(n: int, seed: int = 0) -> list[LabeledPair]:
    rng = random.Random(seed)
    first = ["John", "Jane", "Robert", "Margaret", "William", "Elizabeth", "Michael",
             "Patricia", "David", "Jennifer", "Acme", "Globex", "Initech", "Umbrella"]
    last = ["Smith", "Jones", "Chen", "Warren", "Gates", "Brown", "Corporation",
            "Industries", "Holdings", "Systems", "Partners", "Group"]

    def corrupt(s: str) -> str:
        cs = list(s)
        op = rng.random()
        i = rng.randrange(len(cs))
        if op < 0.4 and len(cs) > 2:
            del cs[i]
        elif op < 0.7:
            cs[i] = rng.choice("abcdefghijklmnopqrstuvwxyz")
        elif op < 0.85 and i < len(cs) - 1:
            cs[i], cs[i + 1] = cs[i + 1], cs[i]
        else:
            cs.insert(i, rng.choice("abcdefghijklmnopqrstuvwxyz"))
        return "".join(cs)

    names = [f"{rng.choice(first)} {rng.choice(last)}" for _ in range(n)]
    pairs: list[LabeledPair] = []
    for nm in names:
        pairs.append((nm, corrupt(nm), 1))
        other = rng.choice(names)
        pairs.append((nm, other, 1 if other == nm else 0))
    return pairs


def _febrl3_pairs(seed: int = 0) -> list[LabeledPair]:
    """Labeled pairs from the recordlinkage Febrl3 dedup dataset (generated, no
    download). Positives = true links; negatives = sampled non-links."""
    from recordlinkage.datasets import load_febrl3

    df, links = load_febrl3(return_links=True)
    fields = ["given_name", "surname", "address_1", "suburb", "state", "postcode"]

    def text(rid: str) -> str:
        r = df.loc[rid]
        return " ".join(str(r[f]) for f in fields if str(r[f]) != "nan")

    ids = list(df.index)
    linkset = {tuple(sorted(p)) for p in links}
    rng = random.Random(seed)
    neg: set[tuple] = set()
    while len(neg) < len(linkset):
        a, b = rng.choice(ids), rng.choice(ids)
        key = tuple(sorted((a, b)))
        if a != b and key not in linkset:
            neg.add(key)
    pos = [(text(a), text(b), 1) for a, b in linkset]
    negs = [(text(a), text(b), 0) for a, b in neg]
    return pos + negs


def _load_pairs(path: str) -> list[LabeledPair]:
    import polars as pl

    df = pl.read_csv(path, encoding="utf8-lossy", ignore_errors=True)
    return [(str(a), str(b), int(y))
            for a, b, y in zip(df["text_a"], df["text_b"], df["label"])]


def _cosines(model: GoldenEmbedModel, data: list[LabeledPair]) -> tuple[np.ndarray, np.ndarray]:
    ea = model.embed([a for a, _b, _y in data])
    eb = model.embed([b for _a, b, _y in data])
    return np.sum(ea * eb, axis=1), np.array([y for _a, _b, y in data])


def _f1(cos: np.ndarray, y: np.ndarray, thr: float) -> float:
    pred = cos >= thr
    tp = int(np.sum(pred & (y == 1)))
    fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1)))
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def _best_thr(cos: np.ndarray, y: np.ndarray) -> float:
    return max(np.linspace(-0.2, 1.0, 61), key=lambda t: _f1(cos, y, float(t)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--febrl3", action="store_true")
    ap.add_argument("--synthetic", type=int, metavar="N")
    ap.add_argument("--pairs", type=str, metavar="FILE")
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--n-features", type=int, default=4096)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    if args.febrl3:
        dataset, pairs = "febrl3", _febrl3_pairs(seed=args.seed)
    elif args.synthetic:
        dataset, pairs = f"synthetic({args.synthetic})", _synthetic_pairs(args.synthetic, args.seed)
    elif args.pairs:
        dataset, pairs = args.pairs, _load_pairs(args.pairs)
    else:
        ap.error("pass --febrl3, --synthetic N, or --pairs FILE")

    rng = random.Random(args.seed)
    rng.shuffle(pairs)
    cut = int((1.0 - args.test_frac) * len(pairs))
    train, test = pairs[:cut], pairs[cut:]
    fc = FeaturizerConfig(n_features=args.n_features)

    # Untrained = random-projection char-n-gram (pure lexical baseline).
    untrained = GoldenEmbedModel(EmbedModelConfig(dim=args.dim, featurizer=fc), seed=args.seed)
    ut_tr_cos, ut_tr_y = _cosines(untrained, train)
    ut_te_cos, ut_te_y = _cosines(untrained, test)
    ut_f1 = _f1(ut_te_cos, ut_te_y, _best_thr(ut_tr_cos, ut_tr_y))

    model, report = train_embedder(
        train, TrainConfig(dim=args.dim, epochs=args.epochs, seed=args.seed, featurizer=fc)
    )
    tr_tr_cos, tr_tr_y = _cosines(model, train)
    tr_te_cos, tr_te_y = _cosines(model, test)
    tr_f1 = _f1(tr_te_cos, tr_te_y, _best_thr(tr_tr_cos, tr_tr_y))

    pos_rate = float(np.mean([y for _a, _b, y in test]))
    floor = 2 * pos_rate / (pos_rate + 1.0)

    out = [
        f"### in-house embedder — `{dataset}`",
        "",
        f"- pairs: {len(pairs)} ({len(test)} held-out test), positives {pos_rate:.0%}",
        f"- dim {args.dim}, n_features {args.n_features}, epochs {args.epochs}",
        f"- train separation: {report.separation_before:.3f} -> {report.separation_after:.3f}",
        "",
        "| model | held-out pairwise F1 |",
        "|---|---|",
        f"| no-signal floor (predict-all) | {floor:.3f} |",
        f"| untrained (lexical n-gram) | {ut_f1:.3f} |",
        f"| **trained in-house** | **{tr_f1:.3f}** |",
        "",
        f"training lift vs lexical: **{tr_f1 - ut_f1:+.3f}**",
    ]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
