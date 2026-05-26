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
  --febrl3              real ER data via the recordlinkage library (no download).
  --leipzig NAME --datasets-dir DIR
                        a Leipzig two-file linkage benchmark (abt-buy /
                        amazon-google / dblp-acm). Negatives are **hard**
                        (nearest lexical non-match), so the task is
                        discriminating — random negatives are trivially
                        separable and saturate F1. Product datasets
                        (abt-buy / amazon-google) are where a better-trained
                        embedder beats the lexical baseline (#507).
  --synthetic N         self-contained corrupted-name pairs.
  --pairs FILE          CSV with columns text_a,text_b,label.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

import numpy as np
from goldenmatch.embeddings.inhouse import (
    CharNGramFeaturizer,
    EmbedModelConfig,
    FeaturizerConfig,
    GoldenEmbedModel,
    TrainConfig,
    train_embedder,
)

LabeledPair = tuple[str, str, int]

# Leipzig two-file linkage benchmarks: (left_csv, left_id, left_fields,
# right_csv, right_id, right_fields, mapping_csv, map_left_col, map_right_col).
_LEIPZIG = {
    "abt-buy": ("Abt.csv", "id", ["name", "description"],
                "Buy.csv", "id", ["name", "manufacturer", "description"],
                "abt_buy_perfectMapping.csv", "idAbt", "idBuy"),
    "amazon-google": ("Amazon.csv", "id", ["title", "manufacturer", "description"],
                      "GoogleProducts.csv", "id", ["name", "manufacturer", "description"],
                      "Amzon_GoogleProducts_perfectMapping.csv", "idAmazon", "idGoogleBase"),
    "dblp-acm": ("DBLP2.csv", "id", ["title", "authors", "venue", "year"],
                 "ACM.csv", "id", ["title", "authors", "venue", "year"],
                 "DBLP-ACM_perfectMapping.csv", "idDBLP", "idACM"),
}


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


def _leipzig_pairs(dataset: str, datasets_dir: str) -> list[LabeledPair]:
    """Positives from the perfect mapping + one **hard** negative per positive
    (the nearest lexical non-match in the right table). Hard negatives make the
    pairwise task discriminating; random negatives across two product/biblio
    tables share almost no n-grams and saturate F1 near 1.0."""
    csv.field_size_limit(10 ** 7)
    lf, lid, lflds, rf, rid, rflds, mf, mca, mcb = _LEIPZIG[dataset]
    root = Path(datasets_dir)

    def load(fn: str, idc: str, flds: list[str]) -> dict[str, str]:
        with open(root / fn, encoding="latin-1") as f:
            return {r[idc]: " ".join(str(r.get(k, "") or "") for k in flds)
                    for r in csv.DictReader(f)}

    left, right = load(lf, lid, lflds), load(rf, rid, rflds)
    with open(root / mf, encoding="latin-1") as f:
        mp = [(r[mca], str(r[mcb])) for r in csv.DictReader(f)
              if r[mca] in left and str(r[mcb]) in right]

    feat = CharNGramFeaturizer(FeaturizerConfig())
    rk = list(right)
    ri = {b: i for i, b in enumerate(rk)}
    fr = feat.transform([right[b] for b in rk])
    fl = feat.transform([left[a] for a, _b in mp])
    sim = fl @ fr.T  # (n_pos, n_right) lexical cosine
    pairs: list[LabeledPair] = []
    for i, (a, b) in enumerate(mp):
        pairs.append((left[a], right[b], 1))
        row = sim[i].copy()
        row[ri[b]] = -9.0  # exclude the true match
        pairs.append((left[a], right[rk[int(row.argmax())]], 0))
    return pairs


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


def _eval_split(
    model: GoldenEmbedModel, train: list[LabeledPair], test: list[LabeledPair]
) -> tuple[np.ndarray, np.ndarray, float]:
    """Held-out cosines/labels + the threshold tuned on the train split."""
    tr_cos, tr_y = _cosines(model, train)
    te_cos, te_y = _cosines(model, test)
    return te_cos, te_y, _best_thr(tr_cos, tr_y)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--febrl3", action="store_true")
    ap.add_argument("--leipzig", choices=sorted(_LEIPZIG), metavar="NAME")
    ap.add_argument("--datasets-dir", type=str, metavar="DIR")
    ap.add_argument("--synthetic", type=int, metavar="N")
    ap.add_argument("--pairs", type=str, metavar="FILE")
    ap.add_argument("--loss", choices=("cosine", "euclidean"), default="cosine")
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--n-features", type=int, default=4096)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seeds", type=int, default=3,
                    help="average held-out F1 over this many splits (seed, seed+1, ...)")
    args = ap.parse_args(argv)

    if args.febrl3:
        dataset, pairs = "febrl3", _febrl3_pairs(seed=args.seed)
    elif args.leipzig:
        if not args.datasets_dir:
            ap.error("--leipzig requires --datasets-dir DIR")
        dataset, pairs = f"{args.leipzig} (hard-neg)", _leipzig_pairs(args.leipzig, args.datasets_dir)
    elif args.synthetic:
        dataset, pairs = f"synthetic({args.synthetic})", _synthetic_pairs(args.synthetic, args.seed)
    elif args.pairs:
        dataset, pairs = args.pairs, _load_pairs(args.pairs)
    else:
        ap.error("pass --febrl3, --leipzig NAME, --synthetic N, or --pairs FILE")

    fc = FeaturizerConfig(n_features=args.n_features)

    # Split by *group* so an entity's positive and its hard negative never land
    # on opposite sides of the split (that leaks the entity and inflates test
    # error). Leipzig pairs are emitted as consecutive (positive, hard-negative)
    # per left record; every other mode is one pair per group.
    if args.leipzig:
        groups = [pairs[i:i + 2] for i in range(0, len(pairs), 2)]
    else:
        groups = [[p] for p in pairs]

    def _one_split(seed: int) -> tuple[float, float, float]:
        gs = list(groups)
        random.Random(seed).shuffle(gs)
        cut = int((1.0 - args.test_frac) * len(gs))
        tr = [p for g in gs[:cut] for p in g]
        te = [p for g in gs[cut:] for p in g]
        # Untrained = random-projection char-n-gram (pure lexical baseline).
        untrained = GoldenEmbedModel(EmbedModelConfig(dim=args.dim, featurizer=fc), seed=seed)
        ut = _f1(*_eval_split(untrained, tr, te))
        model, _ = train_embedder(
            tr,
            TrainConfig(dim=args.dim, epochs=args.epochs, seed=seed, loss=args.loss, featurizer=fc),
        )
        trn = _f1(*_eval_split(model, tr, te))
        pos = float(np.mean([y for _a, _b, y in te]))
        return ut, trn, pos

    results = [_one_split(args.seed + i) for i in range(args.seeds)]
    ut_f1 = float(np.mean([r[0] for r in results]))
    tr_f1 = float(np.mean([r[1] for r in results]))
    floor = float(np.mean([2 * r[2] / (r[2] + 1.0) for r in results]))

    out = [
        f"### in-house embedder — `{dataset}`",
        "",
        f"- pairs: {len(pairs)}, positives {float(np.mean([y for _a, _b, y in pairs])):.0%}, "
        f"{args.seeds}-split mean held-out F1",
        f"- dim {args.dim}, n_features {args.n_features}, epochs {args.epochs}, loss {args.loss}",
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
