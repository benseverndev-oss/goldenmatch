#!/usr/bin/env python
"""Layer-1 probe: the geometry of the 1.5B ER-matcher's 'same-entity' decision.

Tests the core mechanistic-interpretability claim -- that a *concept* is a linear
combination of a FEW primitive directions in the model's latent space -- against
our actual fine-tuned matcher, at the decision site.

METHOD (correlational, final-layer):
  1. Build a balanced probe set of record pairs: true matches + HARD negatives
     (blocking look-alikes -- different person, SAME surname soundex -- the pairs
     the model actually has to work for; random negatives are trivially separable
     and inflate every metric, so we do NOT use them by default).
  2. For each pair, render the EXACT decision prompt the model scores
     (``build_chat`` -> Qwen chat template up to ``<|im_start|>assistant``) and take
     the LAST-TOKEN final-layer hidden state -- the vector the model reads to emit
     ``{"match": ...}``. This is the decision representation.
  3. Three geometric probes on those vectors:
       - linear separability     : 5-fold logistic-probe accuracy (is the decision
                                    linearly DECODABLE at all?)
       - single-direction axis   : diff-of-means direction FIT ON TRAIN, AUC scored
                                    OUT-OF-SAMPLE (is there ONE 'match primitive'
                                    direction, and does it generalize?)
       - effective dimensionality: probe accuracy from the top-k PCA components
                                    (how FEW basis directions carry the concept?)

BOUNDARY (be honest): this is CORRELATIONAL and FINAL-LAYER. llama.cpp exposes the
last-layer hidden state only -- not the per-layer residual stream, and there is no
causal intervention. It proves the primitive is DECODABLE and low-dimensional; it
does NOT prove the model USES that direction to decide, nor where across depth it
forms. Locking Layer 1 (dictionary learning / SAE on the residual stream + causal
steering/ablation) needs the fp16 model with hooks on GPU -- the Modal path, not
this box. See ``docs/design/2026-08-02-15b-decision-geometry-layer1.md``.

The pure helpers (``mine_probe_pairs`` + the three ``probe_*`` fns) are unit-tested
in ``scripts/er_matcher/test_decision_geometry.py`` and need NO model.
``extract_reps`` / ``main`` need llama-cpp-python + the pinned GGUF and are CPU-slow
(~1.4s/pair).

Usage:
    GOLDENMATCH_LOCAL_LLM=1 python -m scripts.er_matcher.interp.decision_geometry \\
        --data <historical_50k.parquet> [--per-class 160] [--negatives hard|random]
"""

from __future__ import annotations

import argparse
import os
import random
from typing import Any

import numpy as np

# Person fields serialized into the decision prompt (historical_50k shape).
DEFAULT_FIELDS = ["first_name", "surname", "dob", "birth_place", "postcode_fake", "occupation"]


def mine_probe_pairs(
    gold: list[Any],
    surname_key: list[str],
    per_class: int,
    *,
    negatives: str = "hard",
    seed: int = 0,
) -> list[tuple[int, int, int]]:
    """Deterministically build ``per_class`` true-match + ``per_class`` non-match
    pairs as ``(idx_a, idx_b, label)`` with label 1=match, 0=non-match.

    ``negatives='hard'`` mines look-alikes: different cluster but SAME
    ``surname_key`` (the phonetic blocking key) -- the discriminative regime.
    ``negatives='random'`` draws arbitrary cross-cluster pairs (the easy regime;
    kept only as a contrast baseline).

    Pure + deterministic given ``seed`` (no model, no I/O) so it is unit-testable
    and reproducible. Raises ValueError if the data cannot supply enough pairs.
    """
    if per_class <= 0:
        raise ValueError("per_class must be positive")
    if len(gold) != len(surname_key):
        raise ValueError("gold and surname_key must align")

    rng = random.Random(seed)
    by_cluster: dict[Any, list[int]] = {}
    for i, g in enumerate(gold):
        by_cluster.setdefault(g, []).append(i)

    matches: list[tuple[int, int]] = []
    clusters = list(by_cluster.items())
    rng.shuffle(clusters)
    for _g, mem in clusters:
        if len(mem) >= 2:
            a, b = rng.sample(mem, 2)
            matches.append((a, b))
        if len(matches) >= per_class:
            break
    if len(matches) < per_class:
        raise ValueError(f"only {len(matches)} match pairs available, need {per_class}")

    negs: list[tuple[int, int]] = []
    if negatives == "hard":
        by_surn: dict[str, list[int]] = {}
        for i, sc in enumerate(surname_key):
            if sc:
                by_surn.setdefault(sc, []).append(i)
        buckets = [b for b in by_surn.values() if len(b) >= 2]
        rng.shuffle(buckets)
        seen_neg: set[tuple[int, int]] = set()
        for bucket in buckets:
            rng.shuffle(bucket)
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    a, b = bucket[i], bucket[j]
                    if gold[a] != gold[b]:
                        key = (min(a, b), max(a, b))
                        if key not in seen_neg:
                            seen_neg.add(key)
                            negs.append((a, b))
                    if len(negs) >= per_class:
                        break
                if len(negs) >= per_class:
                    break
            if len(negs) >= per_class:
                break
        if len(negs) < per_class:
            raise ValueError(
                f"only {len(negs)} hard negatives (shared surname key, different "
                f"cluster) available, need {per_class}; try --negatives random"
            )
    elif negatives == "random":
        all_ids = list(range(len(gold)))
        seen: set[tuple[int, int]] = set()
        while len(negs) < per_class:
            a, b = rng.sample(all_ids, 2)
            key = (min(a, b), max(a, b))
            if gold[a] != gold[b] and key not in seen:
                seen.add(key)
                negs.append((a, b))
    else:
        raise ValueError(f"unknown negatives mode: {negatives!r}")

    pairs = [(a, b, 1) for a, b in matches[:per_class]] + [(a, b, 0) for a, b in negs[:per_class]]
    rng.shuffle(pairs)
    return pairs


def probe_linear_separability(X: np.ndarray, y: np.ndarray, *, folds: int = 5) -> float:
    """5-fold logistic-probe accuracy on per-feature-standardized reps.
    0.5 = chance; ->1.0 = the decision is linearly decodable from the reps."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    Xn = (X - X.mean(0)) / (X.std(0) + 1e-8)
    return float(cross_val_score(LogisticRegression(max_iter=2000, C=0.5), Xn, y, cv=folds).mean())


def probe_held_out_direction(
    X: np.ndarray, y: np.ndarray, *, folds: int = 5, seed: int = 0
) -> tuple[float, float]:
    """Diff-of-means direction FIT ON TRAIN, AUC scored on the held-out TEST fold,
    averaged over ``folds``. Returns (mean_auc, std_auc). Out-of-sample by
    construction, so it cannot overfit the axis the way an in-sample AUC does."""
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    aucs: list[float] = []
    for tr, te in StratifiedKFold(folds, shuffle=True, random_state=seed).split(X, y):
        mu1 = X[tr][y[tr] == 1].mean(0)
        mu0 = X[tr][y[tr] == 0].mean(0)
        d = mu1 - mu0
        norm = np.linalg.norm(d)
        if norm == 0:
            continue
        d = d / norm
        aucs.append(float(roc_auc_score(y[te], X[te] @ d)))
    if not aucs:
        raise ValueError("no usable folds for held-out direction")
    return float(np.mean(aucs)), float(np.std(aucs))


def probe_low_rank(
    X: np.ndarray, y: np.ndarray, ks: tuple[int, ...] = (1, 2, 4, 8, 16, 32), *, folds: int = 5
) -> dict[int, float]:
    """Effective dimensionality: 5-fold logistic-probe accuracy using only the
    top-k PCA components of the standardized reps, for each k. A plateau at small k
    means the concept lives in a low-dimensional linear subspace."""
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    Xn = (X - X.mean(0)) / (X.std(0) + 1e-8)
    out: dict[int, float] = {}
    for k in ks:
        if k > X.shape[1]:
            continue
        Z = PCA(n_components=k, random_state=0).fit_transform(Xn)
        out[k] = float(cross_val_score(LogisticRegression(max_iter=2000), Z, y, cv=folds).mean())
    return out


# --------------------------------------------------------------------------- #
# Model-dependent extraction (needs llama-cpp-python + the pinned GGUF).       #
# --------------------------------------------------------------------------- #
def extract_reps(
    llm: Any, rows: dict[int, dict[str, Any]], pairs: list[tuple[int, int, int]]
) -> tuple[np.ndarray, np.ndarray]:
    """Decision-site last-token final-layer hidden state for each pair.

    Renders the EXACT prompt the model scores (``build_chat`` -> Qwen template up
    to the assistant turn) and returns ``(X, y)`` with ``X`` shape
    ``(len(pairs), hidden_dim)``. ``llm`` must be a ``llama_cpp.Llama`` built with
    ``embedding=True``."""
    from goldenmatch.core.er_matcher.prompt import build_chat

    def rep(a: int, b: int) -> np.ndarray:
        msgs = build_chat(rows[a], rows[b])
        s = (
            "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs)
            + "<|im_start|>assistant\n"
        )
        return np.asarray(llm.embed(s))[-1]  # last token = the decision representation

    xs, ys = [], []
    for k, (a, b, t) in enumerate(pairs):
        xs.append(rep(a, b))
        ys.append(t)
        if (k + 1) % 80 == 0:
            print(f"  embedded {k + 1}/{len(pairs)}", flush=True)
    return np.asarray(xs), np.asarray(ys)


def main() -> None:
    import jellyfish
    import polars as pl
    import pyarrow.parquet as pq

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data", required=True, help="parquet with the person fields + a `cluster` gold column"
    )
    ap.add_argument("--per-class", type=int, default=160)
    ap.add_argument("--negatives", choices=["hard", "random"], default="hard")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fields", nargs="*", default=DEFAULT_FIELDS)
    ap.add_argument("--surname-col", default="surname")
    ap.add_argument("--model-path", default=os.environ.get("GOLDENMATCH_LOCAL_LLM_PATH"))
    args = ap.parse_args()

    os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

    raw = pl.from_arrow(pq.read_table(args.data))
    gold = raw["cluster"].to_list()
    rows = {i: {f: (raw[f][i] or "") for f in args.fields} for i in range(len(gold))}
    surname_key = [jellyfish.soundex(str(raw[args.surname_col][i] or "")) for i in range(len(gold))]

    pairs = mine_probe_pairs(
        gold, surname_key, args.per_class, negatives=args.negatives, seed=args.seed
    )
    n_pos = sum(t for *_, t in pairs)
    print(f"pairs: {n_pos} match + {len(pairs) - n_pos} {args.negatives}-neg", flush=True)

    if not args.model_path:
        raise SystemExit(
            "no model path: set GOLDENMATCH_LOCAL_LLM_PATH or pass --model-path to the pinned GGUF"
        )
    from llama_cpp import Llama

    llm = Llama(model_path=args.model_path, embedding=True, n_ctx=1024, verbose=False)
    X, y = extract_reps(llm, rows, pairs)
    print(f"reps: {X.shape}  (final-layer, dim {X.shape[1]})", flush=True)

    acc = probe_linear_separability(X, y)
    mean_auc, std_auc = probe_held_out_direction(X, y, seed=args.seed)
    low = probe_low_rank(X, y)

    print(f"\n[linear separability] 5-fold logistic-probe acc = {acc:.3f}  (0.5=chance)")
    print(f"[single direction, held-out] diff-of-means AUC = {mean_auc:.3f} +/- {std_auc:.3f}")
    for k, a in low.items():
        print(f"[low-rank] top-{k:2d} PCs -> probe acc {a:.3f}")


if __name__ == "__main__":
    main()
