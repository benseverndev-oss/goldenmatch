"""Answer-quality metrics. Pure functions over predictions + gold so each is
unit-testable on tiny fixtures with no LLM. EM/F1 use SQuAD/MuSiQue-style
normalization (lowercase, strip punctuation + articles, collapse whitespace)."""
from __future__ import annotations

import re
import string
from collections import Counter, defaultdict
from collections.abc import Iterable

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)


def _normalize(s: str) -> str:
    s = s.lower().translate(_PUNCT)
    s = _ARTICLES.sub(" ", s)
    return " ".join(s.split())


def exact_match(pred: str, gold: str) -> float:
    return 1.0 if _normalize(pred) == _normalize(gold) else 0.0


def token_f1(pred: str, gold: str) -> float:
    p = _normalize(pred).split()
    g = _normalize(gold).split()
    if not p or not g:
        return 1.0 if p == g else 0.0
    overlap = sum((Counter(p) & Counter(g)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(p)
    recall = overlap / len(g)
    return 2 * precision * recall / (precision + recall)


def supporting_fact_recall(
    retrieved_ids: Iterable[str], gold_ids: Iterable[str]
) -> float:
    gold = set(gold_ids)
    if not gold:
        return 1.0
    return len(gold & set(retrieved_ids)) / len(gold)


def decay_curve(rows: Iterable[tuple[int, float]]) -> dict[int, float]:
    """rows: (hop_count, correct in {0.0,1.0}) -> {hop_count: mean correctness}."""
    by_hop: dict[int, list[float]] = defaultdict(list)
    for hop, correct in rows:
        by_hop[hop].append(correct)
    return {hop: sum(v) / len(v) for hop, v in sorted(by_hop.items())}
