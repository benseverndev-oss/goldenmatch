"""Pairwise entity-resolution metrics.

A *clustering* is a list of clusters, each a list of integer record indices.
Two records are predicted-matched iff they share a cluster. They are a true
match iff they share an ``entity_id``.

We score on PAIRS (the standard ER metric): for the set of all unordered index
pairs, compare the predicted-positive set against the gold-positive set.

Per-class metrics restrict to pairs whose *both* endpoints carry that
``failure_class`` -- so the precision-critical classes (``same_name_collision``,
``temporal_version``, which contain distinct entities with colliding surface
forms) measure how often a resolver wrongly merges them.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class Score:
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 1.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def support(self) -> int:
        """Number of gold-positive pairs in scope (tp + fn)."""
        return self.tp + self.fn


def _pred_positive_pairs(clustering: list[list[int]]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for cluster in clustering:
        for a, b in combinations(sorted(cluster), 2):
            pairs.add((a, b))
    return pairs


def _gold_positive_pairs(
    entity_ids: list[str], scope: set[int] | None = None
) -> set[tuple[int, int]]:
    idx = list(scope) if scope is not None else list(range(len(entity_ids)))
    pairs: set[tuple[int, int]] = set()
    for a, b in combinations(sorted(idx), 2):
        if entity_ids[a] == entity_ids[b]:
            pairs.add((a, b))
    return pairs


def score(
    entity_ids: list[str],
    clustering: list[list[int]],
    scope: set[int] | None = None,
) -> Score:
    """Score a clustering against gold ``entity_ids``.

    ``scope`` restricts BOTH gold and predicted pairs to indices in the set
    (used for per-class slices). A predicted pair counts only if both endpoints
    are in scope.
    """
    gold = _gold_positive_pairs(entity_ids, scope)
    pred = _pred_positive_pairs(clustering)
    if scope is not None:
        pred = {(a, b) for (a, b) in pred if a in scope and b in scope}
    tp = len(gold & pred)
    fp = len(pred - gold)
    fn = len(gold - pred)
    return Score(tp=tp, fp=fp, fn=fn)


def score_by_class(
    entity_ids: list[str],
    failure_classes: list[str],
    clustering: list[list[int]],
) -> dict[str, Score]:
    """Return one :class:`Score` per failure class plus ``"__overall__"``."""
    classes = sorted(set(failure_classes))
    out: dict[str, Score] = {"__overall__": score(entity_ids, clustering)}
    for cls in classes:
        scope = {i for i, c in enumerate(failure_classes) if c == cls}
        out[cls] = score(entity_ids, clustering, scope=scope)
    return out


def clusterings_equal(a: list[list[int]], b: list[list[int]]) -> bool:
    """Partition equality (order-independent) -- used for the determinism check."""
    norm = lambda cl: {frozenset(c) for c in cl if len(c) > 1}
    return norm(a) == norm(b)
