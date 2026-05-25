"""Parity tests for the optional goldenmatch._native acceleration kernels.

Each test runs the same operation through the pure-Python path
(GOLDENMATCH_NATIVE=0) and the native path (GOLDENMATCH_NATIVE=1) and asserts
identical output. Skipped when the native extension isn't built.

Native is gated OFF by default (see core/_native_loader.py): these tests force
it on per-call via the env var, which native_enabled() reads each time.
"""
from __future__ import annotations

import pytest
from goldenmatch.core import _native_loader
from goldenmatch.core.cluster import _severe_bridge_count, build_clusters

pytestmark = pytest.mark.skipif(
    not _native_loader.native_available(),
    reason="goldenmatch._native not built",
)

# (pairs, all_ids) fixtures spanning singletons, chains, cliques, bridges,
# and an oversized cluster (exercises the auto-split path, which stays Python).
_PAIR_FIXTURES = [
    ([], [1, 2, 3]),                                              # all singletons
    ([(1, 2, 0.9)], [1, 2, 3]),                                   # one pair + singleton
    ([(1, 2, 0.9), (3, 4, 0.9), (2, 3, 0.8)], [1, 2, 3, 4]),      # bridge-joined
    ([(1, 2, 0.9), (2, 3, 0.85), (1, 3, 0.95)], [1, 2, 3]),       # clique
    ([(i, i + 1, 0.9) for i in range(1, 20)], list(range(1, 21))),  # long chain
    ([(1, 2, 1.0), (2, 3, 1.0), (10, 11, 0.7)], [1, 2, 3, 10, 11, 99]),
]


def _normalize(result: dict) -> set:
    """Membership + size + oversized projection, order-independent."""
    return {
        (frozenset(c["members"]), c["size"], c["oversized"])
        for c in result.values()
    }


@pytest.mark.parametrize("pairs,all_ids", _PAIR_FIXTURES)
def test_build_clusters_membership_parity(monkeypatch, pairs, all_ids):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = build_clusters(list(pairs), all_ids=list(all_ids))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = build_clusters(list(pairs), all_ids=list(all_ids))
    assert _normalize(py) == _normalize(native)
    # cluster_quality must match cluster-for-cluster (ids are deterministic).
    assert {cid: c["cluster_quality"] for cid, c in py.items()} == \
           {cid: c["cluster_quality"] for cid, c in native.items()}


_BRIDGE_FIXTURES = [
    ([1, 2, 3, 4], {(1, 2): 0.9, (3, 4): 0.9, (2, 3): 0.8}, 1),    # one severe bridge
    ([1, 2, 3], {(1, 2): 0.9, (2, 3): 0.9, (1, 3): 0.9}, 0),       # clique, none
    ([1, 2, 3], {(1, 2): 0.9, (2, 3): 0.9}, 0),                    # 3-chain, none
    # 6-node chain: removing (2,3)/(3,4)/(4,5) each splits into two >=2 sides.
    ([1, 2, 3, 4, 5, 6], {(1, 2): 0.9, (2, 3): 0.9, (3, 4): 0.7,
                          (4, 5): 0.9, (5, 6): 0.9}, 3),
]


@pytest.mark.parametrize("members,pair_scores,expected", _BRIDGE_FIXTURES)
def test_severe_bridge_count_parity(monkeypatch, members, pair_scores, expected):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = _severe_bridge_count(members, pair_scores)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = _severe_bridge_count(members, pair_scores)
    assert py == native == expected


_CONF_FIXTURES = [
    ({}, 1),                                                       # singleton
    ({}, 3),                                                       # size>1, no edges
    ({(1, 2): 0.9}, 2),                                            # single edge
    ({(1, 2): 0.9, (2, 3): 0.3, (1, 3): 0.95}, 3),                 # full triangle
    ({(1, 2): 0.5, (3, 4): 0.5, (2, 3): 0.5}, 4),                  # tie on min -> first wins
    ({(1, 2): 0.8, (2, 3): 0.6}, 3),                               # partial connectivity
]


@pytest.mark.parametrize("pair_scores,size", _CONF_FIXTURES)
def test_cluster_confidence_parity(monkeypatch, pair_scores, size):
    from goldenmatch.core.cluster import compute_cluster_confidence
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = compute_cluster_confidence(dict(pair_scores), size)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = compute_cluster_confidence(dict(pair_scores), size)
    assert py["bottleneck_pair"] == native["bottleneck_pair"]
    for key in ("min_edge", "avg_edge", "connectivity", "confidence"):
        if py[key] is None:
            assert native[key] is None
        else:
            assert native[key] == pytest.approx(py[key], abs=1e-12)


def test_scorers_match_rapidfuzz():
    """Phase 2: the native scorers must match Python rapidfuzz (what
    core/scorer.py::score_field uses) bit-for-bit, else block-scoring parity
    is impossible. Covers edge cases + a seeded random battery."""
    import random
    import string

    from rapidfuzz.distance import JaroWinkler, Levenshtein
    from rapidfuzz.fuzz import token_sort_ratio as rf_token_sort_ratio

    n = _native_loader.native_module()

    fixed = [
        ("", ""), ("a", ""), ("", "b"), ("abc", "abc"),
        ("John Smith", "Jon Smyth"), ("Smith John", "John Smith"),
        ("café", "cafe"), ("中文字", "中文学"), ("  a  b ", "b a"),
        ("MacDonald", "Macdonald"), ("12345", "12354"),
    ]
    rng = random.Random(12345)
    alphabet = string.ascii_letters + "  éü中0"

    def rand_str() -> str:
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 16)))

    pairs = fixed + [(rand_str(), rand_str()) for _ in range(2000)]
    for a, b in pairs:
        assert n.jaro_winkler_similarity(a, b) == pytest.approx(
            JaroWinkler.similarity(a, b), abs=1e-9), f"jaro_winkler {a!r} {b!r}"
        assert n.levenshtein_similarity(a, b) == pytest.approx(
            Levenshtein.normalized_similarity(a, b), abs=1e-9), f"levenshtein {a!r} {b!r}"
        assert n.token_sort_ratio(a, b) == pytest.approx(
            rf_token_sort_ratio(a, b), abs=1e-9), f"token_sort {a!r} {b!r}"


def test_native_off_by_default(monkeypatch):
    # Unset env -> "auto" -> Python (no component gated on yet): default-safe.
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert _native_loader.native_enabled("clustering") is False


def test_native_required_mode_uses_native(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    assert _native_loader.native_enabled("clustering") is True
