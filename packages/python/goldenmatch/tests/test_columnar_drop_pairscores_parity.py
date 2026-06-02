"""SP4: the gated columnar ``build_clusters`` returns ``pair_scores={}`` on every
cluster (no eager per-cluster dicts -- the SP1 bench loss), while EVERYTHING ELSE
(members-as-set, size, oversized, confidence EXACT float, bottleneck_pair,
cluster_quality, cluster ids) stays byte-identical to the dict path. Scores are
served by a ClusterPairScores view (built at the pipeline level); this test only
gates the build's dict shape.

Native path reads confidence/min/avg from the kernel metadata (deduped, pairs-input
order); off-native uses a transient pairs-order fill. Both byte-identical to the
dict path on everything but pair_scores.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.cluster import build_clusters


def _adversarial_pairs():
    # Mirror tests/test_columnar_cluster_build_parity.py::_adversarial_pairs:
    # singleton id 0, {1,2}, fully-connected {3,4,5}, weak chain {6,7,8}, barbell
    # oversized that splits (10..16 minus 13), score-tied (20,21,22), dup pair,
    # dense clique that can't cleanly split (30..36). PLUS a different-score
    # duplicate canonical pair (3,4) to exercise the kernel last-wins dedup.
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (3, 4, 0.91),                               # different-score dup (last-wins)
        (6, 7, 0.99), (7, 8, 0.40),
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (14, 15, 0.99), (15, 16, 0.99), (14, 16, 0.99),
        (12, 14, 0.31),
        (20, 21, 0.5), (20, 22, 0.5),
        (1, 2, 0.95),                               # same-score dup
        (30, 31, 0.99), (30, 32, 0.99), (30, 33, 0.99), (30, 34, 0.99),
        (30, 35, 0.99), (30, 36, 0.99), (31, 32, 0.99), (31, 33, 0.99),
        (31, 34, 0.99), (31, 35, 0.99), (31, 36, 0.99), (32, 33, 0.99),
        (32, 34, 0.99), (32, 35, 0.99), (32, 36, 0.99), (33, 34, 0.99),
        (33, 35, 0.99), (33, 36, 0.99), (34, 35, 0.99), (34, 36, 0.99),
        (35, 36, 0.99),
    ]
    all_ids = list(range(0, 23)) + list(range(30, 37))
    return pairs, all_ids


def _norm(cinfo: dict) -> dict:
    # members compared as a SET (separate UF / native member-order is arbitrary,
    # PR #598). pair_scores is INTENTIONALLY dropped on the columnar path -> strip
    # it from BOTH sides (the columnar dict carries {}, the dict path carries the
    # real scores; we are NOT asserting those are equal here). Everything else
    # (size/oversized/confidence/bottleneck_pair/cluster_quality) byte-identical.
    out = {k: v for k, v in cinfo.items() if k not in ("members", "pair_scores", "_was_split")}
    out["members"] = frozenset(cinfo["members"])
    return out


@pytest.mark.parametrize("native", ["1", "0"])
def test_columnar_drop_pairscores_byte_identical(monkeypatch, native):
    pairs, all_ids = _adversarial_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)

    if native == "1":
        from goldenmatch.core._native_loader import native_module
        nm = native_module()
        if nm is None or getattr(nm, "build_clusters_arrow", None) is None:
            pytest.skip(
                "native cluster kernel absent; native=1 validated in CI's fresh "
                "native build"
            )

    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "0")
    off = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                         weak_cluster_threshold=0.3, auto_split=True)
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")
    on = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                        weak_cluster_threshold=0.3, auto_split=True)

    assert on.keys() == off.keys()
    for cid in off:
        assert _norm(on[cid]) == _norm(off[cid]), (
            f"cluster {cid} differs:\n on={on[cid]}\n off={off[cid]}"
        )
        # SP4: the columnar dict drops per-cluster pair_scores.
        assert on[cid]["pair_scores"] == {}, f"cluster {cid} pair_scores not empty"
