"""Self-contained trainer for the drug SynonymModel (GS2).

A numpy logistic regression over morphological pair-features (char 2/3-gram
Jaccard, Jaro-Winkler, shared-prefix ratio, length ratio + bias), trained on the
committed PUBLIC pairs (`data/drug_synonyms.train.jsonl`). Genuinely trained, but
its ceiling is the features' signal: morphological synonyms (spelling/salt) are
learnable; arbitrary brand<->generic (Advil<->ibuprofen) has no morphological
signal, so the model can't learn it (the measured ceiling). numpy + rapidfuzz
only; CPU-instant; seeded → reproducible (committed weights re-derive exactly).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from rapidfuzz.distance import JaroWinkler

_DATA = Path(__file__).resolve().parent / "data" / "drug_synonyms.train.jsonl"
_MODEL = Path(__file__).resolve().parent / "data" / "drug_synonym_model.json"

FEATURES = ["jaccard2", "jaccard3", "jw", "prefix_ratio", "len_ratio", "bias"]


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def _ngrams(s: str, n: int) -> set[str]:
    s = s.replace(" ", "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: str, b: str, n: int) -> float:
    ga, gb = _ngrams(a, n), _ngrams(b, n)
    if not ga and not gb:
        return 1.0
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def pair_features(a: str, b: str) -> np.ndarray:
    a, b = _norm(a), _norm(b)
    jw = float(JaroWinkler.similarity(a, b))
    p = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            p += 1
        else:
            break
    denom = max(len(a), len(b), 1)
    prefix_ratio = p / denom
    len_ratio = min(len(a), len(b)) / denom
    return np.array(
        [_jaccard(a, b, 2), _jaccard(a, b, 3), jw, prefix_ratio, len_ratio, 1.0],
        dtype=float,
    )


def load_groups(path: str | Path | None = None) -> list[list[str]]:
    p = Path(path) if path else _DATA
    groups: list[list[str]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        d = json.loads(line)
        if "generic" not in d:  # skip the _meta line
            continue
        groups.append([d["generic"], *d.get("brands", [])])
    return groups


def build_examples(groups: list[list[str]], seed: int = 0, neg_ratio: int = 2):
    rng = np.random.default_rng(seed)
    pos = [(g[i], g[j]) for g in groups for i in range(len(g)) for j in range(i + 1, len(g))]
    members = [(gi, m) for gi, g in enumerate(groups) for m in g]
    neg: list[tuple[str, str]] = []
    target, tries = len(pos) * neg_ratio, 0
    while len(neg) < target and tries < target * 50:
        tries += 1
        (ga, a) = members[int(rng.integers(len(members)))]
        (gb, b) = members[int(rng.integers(len(members)))]
        if ga != gb:
            neg.append((a, b))
    X = np.array([pair_features(a, b) for a, b in pos + neg])
    y = np.array([1.0] * len(pos) + [0.0] * len(neg))
    return X, y


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def train(X: np.ndarray, y: np.ndarray, seed: int = 0, iters: int = 3000, lr: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w = rng.normal(0.0, 0.01, X.shape[1])
    n = len(y)
    for _ in range(iters):
        grad = X.T @ (_sigmoid(X @ w) - y) / n
        w -= lr * grad
    return w


def train_default(seed: int = 0) -> np.ndarray:
    X, y = build_examples(load_groups(), seed=seed)
    return train(X, y, seed=seed)


def save_model(weights: np.ndarray, path: str | Path | None = None, seed: int = 0) -> None:
    p = Path(path) if path else _MODEL
    p.write_text(
        json.dumps(
            {"features": FEATURES, "weights": [float(x) for x in weights], "seed": seed},
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    w = train_default(seed=0)
    save_model(w, seed=0)
    print("trained:", dict(zip(FEATURES, [round(float(x), 4) for x in w])))
