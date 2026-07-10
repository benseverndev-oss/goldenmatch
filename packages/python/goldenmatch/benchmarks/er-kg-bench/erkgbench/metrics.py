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

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

_WS = re.compile(r"\s+")


def _norm_surface(s: str) -> str:
    return _WS.sub(" ", s.strip().casefold())


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


@dataclass(frozen=True)
class SplitRate:
    """Homograph split-rate (the CLEAR-KG headline sub-metric).

    Of gold pairs that share a normalized SURFACE FORM but belong to DIFFERENT
    entities (real homographs), the fraction the resolver correctly keeps in
    different clusters. Goes to ~0 for every ``if similar: merge`` mechanism
    (they resolve on the string, so two identical surfaces always merge); stays
    high for neighborhood/collective ER. Complements the per-class precision on
    ``same_name_collision`` by scoring the merge decision *pairwise on the
    surface collision itself*, independent of the failure-class labelling.
    """

    split: int
    confusable: int

    @property
    def rate(self) -> float:
        return self.split / self.confusable if self.confusable else 1.0


def homograph_split_rate(
    mentions: list[str],
    entity_ids: list[str],
    clustering: list[list[int]],
) -> SplitRate:
    """Fraction of same-surface / different-entity pairs kept apart.

    Records absent from every listed cluster are treated as singletons (their
    own predicted cluster), matching the pairwise convention in :func:`score`.
    """
    cid: dict[int, int] = {}
    for k, cluster in enumerate(clustering):
        for i in cluster:
            cid[i] = k
    next_id = len(clustering)

    def pred(i: int) -> int:
        nonlocal next_id
        if i not in cid:
            cid[i] = next_id
            next_id += 1
        return cid[i]

    by_surface: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(mentions):
        by_surface[_norm_surface(m)].append(i)

    split = confusable = 0
    for idxs in by_surface.values():
        for a, b in combinations(idxs, 2):
            if entity_ids[a] != entity_ids[b]:  # a homograph-confusable pair
                confusable += 1
                if pred(a) != pred(b):
                    split += 1
    return SplitRate(split=split, confusable=confusable)


def clusterings_equal(a: list[list[int]], b: list[list[int]]) -> bool:
    """Partition equality (order-independent) -- used for the determinism check."""
    norm = lambda cl: {frozenset(c) for c in cl if len(c) > 1}
    return norm(a) == norm(b)
