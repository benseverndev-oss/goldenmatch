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

    @classmethod
    def from_pairs(
        cls,
        pairs: Iterable[tuple[int, int, float]],
        clusters: dict[int, dict],
    ) -> ClusterPairScores:
        """Build the view from the RAW input pairs + final cluster membership --
        used when the columnar build returns ``pair_scores={}`` (SP4). Per cluster
        collects the pairs whose BOTH endpoints are in that cluster, in INPUT order
        with LAST-WINS overwrite, keyed by ``(id_a, id_b)`` as given. This
        reproduces the dict path's per-cluster ``pair_scores`` BYTE-IDENTICALLY (the
        eager fill is the same input-order last-wins; cross-cut edges of a split
        cluster have an endpoint outside its members and are excluded -- the #681
        single-cluster argument).

        MUST be the RAW pairs (the same list fed to ``build_clusters``), NOT the
        ``dedup_pairs_max_score`` (max-score, sorted) ``scored_pairs`` field --
        those differ from the dict path's last-wins on different-score duplicate
        canonical pairs.
        """
        member_to_cid: dict[int, int] = {}
        for cid, info in clusters.items():
            for m in info.get("members", []):
                member_to_cid[m] = cid
        by_cid: dict[int, dict[tuple[int, int], float]] = {}
        for a, b, s in pairs:
            ca = member_to_cid.get(a)
            if ca is not None and ca == member_to_cid.get(b):
                by_cid.setdefault(ca, {})[(a, b)] = s
        return cls(by_cid)

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        for cid, ps in self._by_cid.items():
            yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        return self._by_cid.get(cid, {}).get((min(a, b), max(a, b)))
