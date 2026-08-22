"""Measure MGSA-JW against the comparators goldenmatch already ships.

The decision this harness exists to inform: MGSA-JW's headline result is stated
against JW-class comparators, but goldenmatch never runs Jaro-Winkler alone --
``token_sort`` is a shipped scorer with a Rust kernel, and autoconfig can weight
both into an ensemble on a name field. The token-swap collapse the paper fixes
is therefore already covered here. So the question is NOT "does MGSA-JW beat
Jaro-Winkler" (it does, visibly, on a two-line smoke test) but "does one
comparator beat jw + token_sort together" -- on quality, and at what cost per
pair. Only that second question justifies a Rust kernel and a parity entry.

Ensembles are represented by ``max`` and ``mean`` of jw/token_sort. Those are
proxies for a learned weighting, and a tuned ensemble would do somewhat better
than either, so treat them as a FLOOR on the ensemble baseline: MGSA-JW needs to
clear them by enough that a tuned combination would not have closed the gap.

Usage
-----
    PYTHONPATH=. uv run python -m scripts.mgsa_jw.bench --datasets all
    PYTHONPATH=. uv run python -m scripts.mgsa_jw.bench --datasets historical_50k --max-pos 500
"""

from __future__ import annotations

import argparse
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import polars as pl

from scripts.mgsa_jw.mgsa import convjw, mgsa_jw

# ── which text each dataset compares ─────────────────────────────────────────
# A single string per row: this benchmarks a COMPARATOR, not a whole matcher, so
# every scorer sees identical input and the ranking is attributable to the
# comparator alone.
NAME_FIELDS: dict[str, Sequence[str]] = {
    "anchor_person_match": ("first_name", "last_name"),
    "synthetic": ("first_name", "last_name"),
    "orgs_hard": ("org_name",),
    "dblp_acm": ("title",),
    "febrl3": ("given_name", "surname"),
    "ncvr_synthetic": ("first_name", "last_name"),
    "historical_50k": ("full_name",),
}


def _row_text(df: pl.DataFrame, fields: Sequence[str]) -> list[str]:
    present = [f for f in fields if f in df.columns]
    if not present:
        raise KeyError(f"none of {fields} in {df.columns}")
    cols = [df[f].cast(pl.Utf8).fill_null("").to_list() for f in present]
    return [" ".join(p for p in parts if p).strip().lower() for parts in zip(*cols)]


# ── pair sampling ────────────────────────────────────────────────────────────


def _hard_negatives(
    texts: list[str],
    positives: set[tuple[int, int]],
    want: int,
    rng: random.Random,
) -> list[tuple[int, int]]:
    """Negatives that SHARE A TOKEN with their partner.

    Uniformly random negatives are trivially separable -- every comparator
    scores near 0 and max-F1 saturates for all of them, which would hide exactly
    the differences this benchmark exists to find. Token-sharing pairs are the
    ones a real blocker surfaces and a comparator has to resolve.
    """
    by_token: dict[str, list[int]] = {}
    for idx, text in enumerate(texts):
        for tok in set(text.split()):
            if len(tok) > 2:
                by_token.setdefault(tok, []).append(idx)

    buckets = [v for v in by_token.values() if 2 <= len(v) <= 400]
    rng.shuffle(buckets)

    out: set[tuple[int, int]] = set()
    for bucket in buckets:
        for _ in range(min(len(bucket) * 2, 64)):
            a, b = rng.sample(bucket, 2)
            pair = (min(a, b), max(a, b))
            if pair not in positives and texts[a] and texts[b]:
                out.add(pair)
            if len(out) >= want:
                return list(out)
    # Top up with random pairs when token-sharing candidates run out.
    n = len(texts)
    guard = 0
    while len(out) < want and guard < want * 200:
        guard += 1
        a, b = rng.randrange(n), rng.randrange(n)
        if a == b:
            continue
        pair = (min(a, b), max(a, b))
        if pair not in positives and texts[a] and texts[b]:
            out.add(pair)
    return list(out)


def build_pairs(
    texts: list[str],
    gt: set[tuple[int, int]],
    *,
    max_pos: int,
    neg_ratio: float,
    seed: int,
) -> tuple[list[tuple[str, str]], list[int]]:
    rng = random.Random(seed)
    pos = sorted(p for p in gt if texts[p[0]] and texts[p[1]])
    if len(pos) > max_pos:
        pos = rng.sample(pos, max_pos)
    neg = _hard_negatives(texts, set(gt), int(len(pos) * neg_ratio), rng)

    pairs = [(texts[a], texts[b]) for a, b in pos] + [(texts[a], texts[b]) for a, b in neg]
    labels = [1] * len(pos) + [0] * len(neg)
    return pairs, labels


# ── metrics ──────────────────────────────────────────────────────────────────


def max_f1(scores: list[float], labels: list[int]) -> tuple[float, float]:
    """Best F1 over every threshold, and the threshold that achieves it.

    Sweeps by walking the score-sorted list, so the reported optimum is exact
    for this pair set rather than the best of a fixed grid.
    """
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    total_pos = sum(labels)
    if total_pos == 0:
        return 0.0, 0.0
    tp = 0
    best_f1, best_t = 0.0, 0.0
    for rank, idx in enumerate(order, start=1):
        tp += labels[idx]
        precision = tp / rank
        recall = tp / total_pos
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
            if f1 > best_f1:
                best_f1, best_t = f1, scores[idx]
    return best_f1, best_t


# ── comparators under test ───────────────────────────────────────────────────


def _scorers() -> dict[str, Callable[[str, str], float]]:
    from goldenmatch.core import strsim

    jw = strsim.jaro_winkler_similarity
    ts = strsim.token_sort_similarity
    return {
        "jaro_winkler": lambda a, b: float(jw(a, b)),
        "token_sort": lambda a, b: float(ts(a, b)),
        "ens_max(jw,ts)": lambda a, b: max(float(jw(a, b)), float(ts(a, b))),
        "ens_mean(jw,ts)": lambda a, b: (float(jw(a, b)) + float(ts(a, b))) / 2,
        "convjw": convjw,
        "mgsa_jw": mgsa_jw,
        "mgsa_jw[no-token-peak]": lambda a, b: mgsa_jw(a, b, token_peak=False),
    }


@dataclass
class Result:
    dataset: str
    scorer: str
    f1: float
    threshold: float
    us_per_pair: float


def run_dataset(name: str, loader, *, max_pos: int, neg_ratio: float, seed: int) -> list[Result]:
    loaded = loader()
    if loaded is None:
        print(f"  {name}: absent, skipped")
        return []
    df, gt = loaded
    if not gt:
        print(f"  {name}: no ground truth, skipped")
        return []
    try:
        texts = _row_text(df, NAME_FIELDS[name])
    except KeyError as exc:
        print(f"  {name}: {exc}, skipped")
        return []

    pairs, labels = build_pairs(texts, gt, max_pos=max_pos, neg_ratio=neg_ratio, seed=seed)
    if sum(labels) == 0:
        print(f"  {name}: no usable positive pairs, skipped")
        return []
    print(f"  {name}: {sum(labels)} pos + {len(labels) - sum(labels)} neg")

    results = []
    for scorer_name, fn in _scorers().items():
        start = time.perf_counter()
        scores = [fn(a, b) for a, b in pairs]
        elapsed = time.perf_counter() - start
        f1, threshold = max_f1(scores, labels)
        results.append(Result(name, scorer_name, f1, threshold, elapsed / len(pairs) * 1e6))
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets", default="all", help="comma-separated, or 'all'")
    ap.add_argument("--max-pos", type=int, default=1000)
    ap.add_argument("--neg-ratio", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args()

    from scripts.suggest_quality.datasets import REGISTRY

    wanted = (
        set(NAME_FIELDS)
        if args.datasets == "all"
        else {d.strip() for d in args.datasets.split(",")}
    )
    chosen = [d for d in REGISTRY if d.name in wanted and d.name in NAME_FIELDS]

    print(
        f"pairs: <= {args.max_pos} positives, {args.neg_ratio}x hard negatives, seed {args.seed}\n"
    )
    all_results: list[Result] = []
    for dataset in chosen:
        all_results.extend(
            run_dataset(
                dataset.name,
                dataset.loader,
                max_pos=args.max_pos,
                neg_ratio=args.neg_ratio,
                seed=args.seed,
            )
        )

    if not all_results:
        print("\nno results")
        return

    names = list(_scorers())
    datasets = sorted({r.dataset for r in all_results})
    table = {(r.dataset, r.scorer): r for r in all_results}

    width = max(len(n) for n in names) + 2
    print(f"\n{'max-F1':<{width}}" + "".join(f"{d[:14]:>16}" for d in datasets) + f"{'MEAN':>10}")
    print("-" * (width + 16 * len(datasets) + 10))
    for scorer in names:
        cells, vals = "", []
        for dataset in datasets:
            res = table.get((dataset, scorer))
            if res is None:
                cells += f"{'--':>16}"
            else:
                cells += f"{res.f1:>16.4f}"
                vals.append(res.f1)
        mean = sum(vals) / len(vals) if vals else 0.0
        print(f"{scorer:<{width}}{cells}{mean:>10.4f}")

    print(f"\n{'us/pair':<{width}}" + "".join(f"{d[:14]:>16}" for d in datasets))
    print("-" * (width + 16 * len(datasets)))
    for scorer in names:
        cells = ""
        for dataset in datasets:
            res = table.get((dataset, scorer))
            cells += f"{res.us_per_pair:>16.1f}" if res else f"{'--':>16}"
        print(f"{scorer:<{width}}{cells}")


if __name__ == "__main__":
    main()
