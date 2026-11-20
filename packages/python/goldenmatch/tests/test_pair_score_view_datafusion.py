"""The DataFusion (Arrow-native) pair-score view must be byte-identical to the
Python ``_bucket_pairs`` view it replaces on the identity path.

``ClusterPairScores.from_frames`` built identity's per-cluster edge view by
round-tripping every scored pair through Python. On the B2c path that is a
``to_pydict()`` + ``list(zip(...))`` of the whole Arrow pair table, then either a
per-pair dict bucket (arrow lane) or a per-pair loop back into a Polars frame.
At ~30M scored pairs the transient is several GB and OOMs a 32 GB box right
before identity resolve. Scale-substrate SP1 (#695) built the Arrow edge view
on DataFusion and explicitly deferred wiring it into identity; this is that
wiring.

The LEGACY view's contract is kept exactly -- not SP1's scale-mode MAX
contract: input order, LAST-WINS on a duplicated ``(a, b)``, keys kept as given
(``(7, 3)`` and ``(3, 7)`` are distinct), self-pairs kept, both endpoints in the
same cluster, and each cluster's edges in first-occurrence order. Asserted
against ``ClusterPairScores.from_pairs`` on adversarial and randomized inputs,
then at the identity layer (durable evidence edges) and through the real B2c
pipeline. The loud "datafusion must be installed in CI" guard lives in
``test_cluster_edges_df.py``.
"""

from __future__ import annotations

import random

import polars as pl
import pyarrow as pa
import pytest

pytest.importorskip("datafusion")

from goldenmatch.core.cluster import build_clusters  # noqa: E402
from goldenmatch.core.cluster_edges_df import cluster_pair_scores_datafusion  # noqa: E402
from goldenmatch.core.cluster_pairscores import (  # noqa: E402
    ClusterPairScores,
    build_pair_score_view,
)
from goldenmatch.identity import IdentityStore, resolve_clusters  # noqa: E402


def _adversarial():
    # The identity parity fixture (singleton, triple, weak chain, a barbell
    # that SPLITS at max_cluster_size=5, ties, a same-score duplicate, a dense
    # can't-split clique) plus the cases the legacy contract is precise about:
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (6, 7, 0.99), (7, 8, 0.40),
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (14, 15, 0.99), (15, 16, 0.99), (14, 16, 0.99),
        (12, 14, 0.31),
        (20, 21, 0.5), (20, 22, 0.5),
        (1, 2, 0.95),
        (30, 31, 0.99), (30, 32, 0.99), (30, 33, 0.99), (30, 34, 0.99),
        (30, 35, 0.99), (30, 36, 0.99), (31, 32, 0.99), (31, 33, 0.99),
        (31, 34, 0.99), (31, 35, 0.99), (31, 36, 0.99), (32, 33, 0.99),
        (32, 34, 0.99), (32, 35, 0.99), (32, 36, 0.99), (33, 34, 0.99),
        (33, 35, 0.99), (33, 36, 0.99), (34, 35, 0.99), (34, 36, 0.99),
        (35, 36, 0.99),
        (3, 4, 0.10),    # duplicate with a DIFFERENT score: last wins, first position kept
        (2, 1, 0.50),    # reversed key: a distinct edge, never canonicalized
        (5, 5, 0.70),    # self-pair: kept
    ]
    all_ids = list(range(0, 23)) + list(range(30, 37))
    clusters = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                              weak_cluster_threshold=0.3, auto_split=True)
    # These exercise the VIEW's membership filter, so they go to the view only:
    # fed to build_clusters, 99 is an unknown id and (1, 3) would MERGE the two
    # clusters it is meant to straddle.
    view_only = [
        (99, 1, 0.90),   # endpoint not in the partition: dropped
        (1, 3, 0.90),    # endpoints in different clusters: dropped
    ]
    return pairs + view_only, clusters


def _randomized(seed: int):
    rng = random.Random(seed)
    members = list(range(200))
    rng.shuffle(members)
    clusters: dict[int, dict] = {}
    i, cid = 0, 0
    while i < len(members):
        size = rng.randint(1, 8)
        clusters[cid] = {"members": members[i:i + size]}
        i += size
        cid += 1
    pool = list(range(210))  # a few ids outside the partition
    pairs = [(rng.choice(pool), rng.choice(pool), round(rng.random(), 2)) for _ in range(3000)]
    return pairs, clusters


def _assignments(clusters: dict[int, dict], lane: str):
    cids, mids = [], []
    for c, info in clusters.items():
        for m in info["members"]:
            cids.append(c)
            mids.append(m)
    tbl = pa.table({"cluster_id": pa.array(cids, pa.int64()),
                    "member_id": pa.array(mids, pa.int64())})
    return pl.from_arrow(tbl) if lane == "polars" else tbl


def _assert_same_view(got: ClusterPairScores, want: ClusterPairScores, clusters) -> int:
    n_edges = 0
    for cid in clusters:
        g, w = got.for_cluster(cid), want.for_cluster(cid)
        assert g == w, f"cluster {cid}: {g} != {w}"
        assert list(g) == list(w), f"cluster {cid}: edge ORDER differs"
        for a, b in w:
            assert got.score_for(cid, a, b) == want.score_for(cid, a, b)
            assert got.score_for(cid, b, a) == want.score_for(cid, b, a)
        n_edges += len(w)
    as_map = lambda v: {c: list(e) for c, e in v.iter_clusters()}  # noqa: E731
    assert as_map(got) == as_map(want)
    return n_edges


@pytest.mark.parametrize("lane", ["arrow", "polars"])
def test_matches_from_pairs_adversarial(lane):
    pairs, clusters = _adversarial()
    want = ClusterPairScores.from_pairs(pairs, clusters)
    got = cluster_pair_scores_datafusion(pairs, _assignments(clusters, lane))
    assert _assert_same_view(got, want, clusters) > 0
    # the cases above actually landed: last-wins kept first position, reversed
    # key and self-pair survive, foreign / cross-cluster endpoints dropped
    c345 = next(c for c, i in clusters.items() if {3, 4, 5} <= set(i["members"]))
    edges = got.for_cluster(c345)
    assert edges[(3, 4)] == 0.10 and list(edges)[0] == (3, 4)
    assert edges[(5, 5)] == 0.70
    c12 = next(c for c, i in clusters.items() if {1, 2} <= set(i["members"]))
    assert got.for_cluster(c12) == {(1, 2): 0.95, (2, 1): 0.50}


@pytest.mark.parametrize("lane", ["arrow", "polars"])
@pytest.mark.parametrize("seed", range(5))
def test_matches_from_pairs_randomized(seed, lane):
    pairs, clusters = _randomized(seed)
    want = ClusterPairScores.from_pairs(pairs, clusters)
    got = cluster_pair_scores_datafusion(pairs, _assignments(clusters, lane))
    assert _assert_same_view(got, want, clusters) > 50


def test_arrow_table_input_is_taken_as_is():
    """The B2c pair table (``id_a``/``id_b``/``score``) and an ``a``/``b``/
    ``score`` table both go straight in -- no per-pair Python -- and equal the
    tuple-list result."""
    pairs, clusters = _randomized(11)
    asg = _assignments(clusters, "arrow")
    from_list = cluster_pair_scores_datafusion(pairs, asg)
    cols = list(zip(*pairs))
    b2c = pa.table({"id_a": pa.array(cols[0], pa.int64()), "id_b": pa.array(cols[1], pa.int64()),
                    "score": pa.array(cols[2], pa.float64())})
    plain = b2c.rename_columns(["a", "b", "score"])
    _assert_same_view(cluster_pair_scores_datafusion(b2c, asg), from_list, clusters)
    _assert_same_view(cluster_pair_scores_datafusion(plain, asg), from_list, clusters)


def test_empty_pairs_and_empty_partition():
    _, clusters = _randomized(3)
    empty = cluster_pair_scores_datafusion([], _assignments(clusters, "arrow"))
    assert empty.for_cluster(0) == {} and list(empty.iter_clusters()) == []
    assert empty.score_for(0, 1, 2) is None
    none = cluster_pair_scores_datafusion([(1, 2, 0.9)], _assignments({}, "arrow"))
    assert list(none.iter_clusters()) == []


def _normalized_edges(store: IdentityStore) -> list[tuple]:
    edges = []
    for node in store.list_identities(limit=10_000):
        for e in store.edges_for_entity(node.entity_id):
            edges.append((e.record_a_id, e.record_b_id, e.kind, e.score))
    return sorted(edges, key=lambda t: (t[0], t[1], t[2], -1.0 if t[3] is None else t[3]))


def test_identity_evidence_edges_byte_identical(tmp_path):
    """The durable layer: resolving identity through the DataFusion view writes
    the same evidence edges as through the Python view."""
    pairs, clusters = _adversarial()
    ids = sorted({m for i in clusters.values() for m in i["members"]})
    df = pl.DataFrame([{"__row_id__": i, "__source__": "src", "id": str(i), "name": f"n{i}"}
                       for i in ids])
    out = {}
    for tag, view in (("py", ClusterPairScores.from_pairs(pairs, clusters)),
                      ("df", cluster_pair_scores_datafusion(pairs, _assignments(clusters, "arrow")))):
        store = IdentityStore(path=str(tmp_path / f"{tag}.db"))
        try:
            resolve_clusters(clusters, df, pairs, "wd", store, run_name=f"run-{tag}",
                             source_pk_col="id", pair_score_view=view)
            out[tag] = _normalized_edges(store)
        finally:
            store.close()
    assert out["py"], "fixture produced no edges -- the comparison would be vacuous"
    assert out["df"] == out["py"]


def test_backend_knob(monkeypatch):
    pairs, clusters = _randomized(5)
    asg = _assignments(clusters, "arrow")
    monkeypatch.setenv("GOLDENMATCH_PAIR_SCORE_VIEW", "python")
    py = build_pair_score_view(asg, pairs)
    assert py._edges is None                       # legacy backing
    monkeypatch.setenv("GOLDENMATCH_PAIR_SCORE_VIEW", "datafusion")
    df = build_pair_score_view(asg, pairs)
    assert df._edges is not None                   # Arrow-backed
    _assert_same_view(df, py, clusters)
    monkeypatch.delenv("GOLDENMATCH_PAIR_SCORE_VIEW")
    assert build_pair_score_view(asg, pairs)._edges is not None   # auto -> datafusion

    import goldenmatch.core.cluster_pairscores as cps
    monkeypatch.setattr(cps, "_datafusion_available", lambda: False)
    assert build_pair_score_view(asg, pairs)._edges is None       # auto falls back
    monkeypatch.setenv("GOLDENMATCH_PAIR_SCORE_VIEW", "datafusion")
    with pytest.raises(ImportError, match="datafusion"):
        build_pair_score_view(asg, pairs)                          # explicit: refuse silently


def test_pipeline_feeds_the_arrow_pair_table(monkeypatch, tmp_path):
    """Through ``dedupe_df`` on the B2c path with identity on: the view is built
    from the Arrow pair table itself (never a Python pair list), and the identity
    store's evidence edges equal the Python-view run."""
    import goldenmatch as gm
    import goldenmatch.core.cluster_edges_df as ce
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        IdentityConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    df = pl.DataFrame({
        "pk": ["a", "b", "c", "d", "e", "f"],
        "first_name": ["john", "john", "jon", "mary", "mary", "zoe"],
        "email": ["j@x.com", "j@x.com", "j@x.com", "m@x.com", "m@x.com", "z@x.com"],
    })
    for k, v in {"GOLDENMATCH_FS_COLUMNAR_CLUSTER": "1", "GOLDENMATCH_FS_WORKERS": "1",
                 "GOLDENMATCH_FS_LINEAR_CUT": "off", "GOLDENMATCH_FS_CUT_ROUTER": "off"}.items():
        monkeypatch.setenv(k, v)

    seen: list = []
    real = ce.cluster_pair_scores_datafusion

    def _spy(pairs, assignments, **kw):
        seen.append(type(pairs))
        return real(pairs, assignments, **kw)

    monkeypatch.setattr(ce, "cluster_pair_scores_datafusion", _spy)

    def _resolve(tag: str, backend: str) -> list[tuple]:
        monkeypatch.setenv("GOLDENMATCH_PAIR_SCORE_VIEW", backend)
        path = str(tmp_path / f"{tag}.db")
        cfg = GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(name="fs", type="probabilistic", fields=[
                MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3,
                              partial_threshold=0.8),
                MatchkeyField(field="email", scorer="exact", levels=2),
            ])],
            blocking=BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=["email"])]),
            backend="bucket",
            identity=IdentityConfig(enabled=True, backend="sqlite", path=path,
                                    source_pk_column="pk"),
        )
        gm.dedupe_df(df, config=cfg, confidence_required=False)
        store = IdentityStore(path=path)
        try:
            return _normalized_edges(store)
        finally:
            store.close()

    py_edges = _resolve("py", "python")
    assert not seen, "python backend must not touch the DataFusion builder"
    df_edges = _resolve("df", "datafusion")
    assert seen and all(t is pa.Table for t in seen), seen
    assert py_edges, "pipeline produced no evidence edges -- comparison would be vacuous"
    assert df_edges == py_edges
