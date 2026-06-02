"""Lazy per-cluster pair-score view (Phase 2 SP2). Decouples the identity
evidence-edge consumer from the legacy per-cluster ``pair_scores`` dict. Sourced
from the FINAL (post-split) cluster partition so it is byte-identical to the dict
path. dict-of-dicts backing; the ``iter_clusters`` interface is frame-ready for a
future SP that makes the build produce a columnar pair frame natively."""
from __future__ import annotations

from collections.abc import Iterable, Iterator


class ClusterPairScores:
    __slots__ = ("_by_cid",)

    def __init__(self, by_cid: dict[int, dict[tuple[int, int], float]]):
        self._by_cid = by_cid

    @classmethod
    def from_cluster_dict(cls, clusters: dict[int, dict]) -> ClusterPairScores:
        by_cid: dict[int, dict[tuple[int, int], float]] = {}
        for cid, info in clusters.items():
            ps = info.get("pair_scores") or {}
            if ps:
                by_cid[cid] = dict(ps)
        return cls(by_cid)

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        for cid, ps in self._by_cid.items():
            yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        return self._by_cid.get(cid, {}).get((min(a, b), max(a, b)))
