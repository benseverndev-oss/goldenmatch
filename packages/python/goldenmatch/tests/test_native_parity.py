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


def test_score_block_pairs_kernel_parity():
    """Phase 2: the native per-pair kernel must match a faithful Python replica
    of score_buckets._score_one_bucket_fast's loop (using real score_field)."""
    import random
    import string

    from goldenmatch.core.scorer import score_field

    n = _native_loader.native_module()
    scorer_ids = {"jaro_winkler": 0, "levenshtein": 1, "token_sort": 2, "exact": 3}
    names = ["jaro_winkler", "token_sort"]
    ids = [scorer_ids[x] for x in names]
    fns = [(lambda nm: (lambda a, b: score_field(a, b, nm)))(nm) for nm in names]
    rng = random.Random(99)

    def rand_val():
        if rng.random() < 0.1:
            return None
        return "".join(rng.choice(string.ascii_letters + " ") for _ in range(rng.randint(2, 8)))

    def reference(row_ids, sizes, fa, weights, total_weight, threshold, exclude):
        out = []
        offset = 0
        for size in sizes:
            if size >= 2:
                end = offset + size
                for i in range(offset, end - 1):
                    ri = row_ids[i]
                    for j in range(i + 1, end):
                        rj = row_ids[j]
                        pk = (ri, rj) if ri < rj else (rj, ri)
                        if pk in exclude:
                            continue
                        ss = ws = 0.0
                        for f in range(len(fns)):
                            va, vb = fa[f][i], fa[f][j]
                            if va is None or vb is None:
                                continue
                            ss += fns[f](va, vb) * weights[f]
                            ws += weights[f]
                        if ws > 0:
                            c = ss / total_weight
                            if c >= threshold:
                                out.append((pk[0], pk[1], float(c)))
            offset += size
        return out

    for _ in range(300):
        nrows = rng.randint(0, 12)
        row_ids = rng.sample(range(1000), nrows)
        sizes, rem = [], nrows
        while rem > 0:
            s = rng.randint(1, min(4, rem))
            sizes.append(s)
            rem -= s
        fa = [[rand_val() for _ in range(nrows)] for _ in names]
        weights = [rng.choice([0.5, 1.0, 2.0]) for _ in names]
        tw = sum(weights)
        threshold = rng.choice([0.0, 0.5, 0.8])
        exclude = set()
        if nrows >= 2 and rng.random() < 0.3:
            a, b = rng.sample(row_ids, 2)
            exclude.add((min(a, b), max(a, b)))
        ref = reference(row_ids, sizes, fa, weights, tw, threshold, exclude)
        got = n.score_block_pairs(row_ids, sizes, fa, ids, weights, tw, threshold, list(exclude))
        assert len(ref) == len(got)
        for (ra, rb, rs), (ga, gb, gs) in zip(ref, got):
            assert (ra, rb) == (ga, gb)
            assert gs == pytest.approx(rs, abs=1e-12)


def test_score_buckets_end_to_end_parity(monkeypatch):
    """Phase 2: score_buckets() must emit identical pairs with the native kernel
    on (GOLDENMATCH_NATIVE=1) vs off (=0)."""
    import polars as pl
    from goldenmatch.backends.score_buckets import score_buckets
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        MatchkeyConfig,
        MatchkeyField,
    )
    from goldenmatch.core.matchkey import _xform_sig

    f1 = MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)
    f2 = MatchkeyField(field="city", scorer="token_sort", weight=2.0)
    mk = MatchkeyConfig(name="m", type="weighted", threshold=0.6, fields=[f1, f2])
    bc = BlockingConfig(keys=[BlockingKeyConfig(fields=["blk"])])

    names = ["John Smith", "Jon Smith", "Jane Doe", "Jayne Doe", "Bob Roe",
             "John Smyth", "Jane Doe", "Robert Roe"]
    cities = ["London", "London", "Leeds", "Leeds", "York",
              "London", "Leeds", "York"]
    blk = ["a", "a", "b", "b", "c", "a", "b", "c"]
    df = pl.DataFrame({
        "__row_id__": list(range(len(names))),
        "blk": blk,
        _xform_sig(f1): names,
        _xform_sig(f2): cities,
    })

    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = score_buckets(df, bc, mk, set())
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = score_buckets(df, bc, mk, set())

    assert len(py) > 0  # not vacuous
    # The native scorers are independent rapidfuzz reimplementations, so scores
    # match within float tolerance (~1e-9), not bit-for-bit. Assert the emitted
    # pair set is identical and per-pair scores agree to tolerance.
    py_scores = {(a, b): s for a, b, s in py}
    native_scores = {(a, b): s for a, b, s in native}
    assert py_scores.keys() == native_scores.keys()
    for pair, s in py_scores.items():
        assert native_scores[pair] == pytest.approx(s, abs=1e-9)


# ── Native Core pair primitives (component "pairs") ──

# Mixed orientations, duplicate canonical pairs with differing scores (exercises
# the max reduction + tie handling), a self-pair, and an empty case.
_PAIRS_FIXTURES = [
    [],
    [(2, 1, 0.5)],
    [(1, 2, 0.9), (2, 1, 0.3), (3, 4, 0.7), (4, 3, 0.7)],
    [(5, 5, 1.0), (1, 2, 0.1), (1, 2, 0.1), (2, 1, 0.8)],
    [(i % 7, (i + 3) % 7, (i % 5) / 5.0) for i in range(50)],
]


@pytest.mark.parametrize("pairs", _PAIRS_FIXTURES)
def test_canonicalize_pairs_parity(monkeypatch, pairs):
    from goldenmatch.core.pairs import canonicalize_pairs
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = canonicalize_pairs(list(pairs))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = canonicalize_pairs(list(pairs))
    assert py == native


@pytest.mark.parametrize("pairs", _PAIRS_FIXTURES)
def test_dedup_pairs_max_score_parity(monkeypatch, pairs):
    from goldenmatch.core.pairs import dedup_pairs_max_score
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = dedup_pairs_max_score(list(pairs))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = dedup_pairs_max_score(list(pairs))
    assert py == native  # bit-exact: integer keys + strict-> max, sorted output


_BLOCK_SIZE_FIXTURES = [
    [],
    [1],
    [1, 1, 1],
    [3, 1, 4, 1, 5, 9, 2, 6],
    [1000, 500, 2, 1, 1, 1, 7],
]


@pytest.mark.parametrize("sizes", _BLOCK_SIZE_FIXTURES)
def test_candidate_pair_count_parity(monkeypatch, sizes):
    from goldenmatch.core.pairs import candidate_pair_count
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = candidate_pair_count(list(sizes))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = candidate_pair_count(list(sizes))
    assert py == native


@pytest.mark.parametrize("sizes", _BLOCK_SIZE_FIXTURES)
def test_block_histogram_parity(monkeypatch, sizes):
    from goldenmatch.core.pairs import block_histogram
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = block_histogram(list(sizes))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = block_histogram(list(sizes))
    assert py == native


@pytest.mark.parametrize("pairs,all_ids", _PAIR_FIXTURES)
def test_connected_components_parity(monkeypatch, pairs, all_ids):
    from goldenmatch.core.pairs import connected_components
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = connected_components(list(pairs), list(all_ids))
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    native = connected_components(list(pairs), list(all_ids))
    assert {frozenset(c) for c in py} == {frozenset(c) for c in native}


def test_native_off_when_forced(monkeypatch):
    # GOLDENMATCH_NATIVE=0 is the kill switch: forces the Python path even for
    # gated-on components.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    assert _native_loader.native_enabled("clustering") is False
    assert _native_loader.native_enabled("block_scoring") is False


def test_auto_uses_native_only_for_gated_components(monkeypatch):
    # Under "auto" (unset), a signed-off component uses native iff the ext is
    # importable; a component that hasn't cleared the gate stays on Python.
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    available = _native_loader.native_available()
    assert _native_loader.native_enabled("clustering") is available
    assert _native_loader.native_enabled("block_scoring") is available
    assert _native_loader.native_enabled("pairs") is available
    assert _native_loader.native_enabled("not_a_real_component") is False


def test_native_required_mode_uses_native(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    assert _native_loader.native_enabled("clustering") is True
