"""Tests for randomized-contraction WCC (#844 Spec 1).

Pure-Polars correctness tests run without Ray; the Ray-orchestration and
routing tests are gated with a function-level importorskip.
"""
import polars as pl
import pytest


def test_rc_prime_is_mersenne():
    from goldenmatch.distributed.clustering import _RC_PRIME
    assert _RC_PRIME == 2**31 - 1


def test_rc_symmetrize_both_directions_no_selfloops():
    from goldenmatch.distributed.clustering import _rc_symmetrize
    pairs = pl.DataFrame({"id_a": [1, 2, 5], "id_b": [2, 3, 5]})
    e = _rc_symmetrize(pairs)
    got = set(zip(e["v"].to_list(), e["w"].to_list()))
    assert got == {(1, 2), (2, 1), (2, 3), (3, 2)}


def test_rc_contract_round_collapses_triangle_neighbor():
    from goldenmatch.distributed.clustering import _rc_contract_round, _rc_symmetrize
    e = _rc_symmetrize(pl.DataFrame({"id_a": [1, 2], "id_b": [2, 3]}))
    contracted, rep = _rc_contract_round(e, A=1, B=0)
    rep_map = dict(zip(rep["v"].to_list(), rep["rep"].to_list()))
    assert rep_map == {1: 1, 2: 1, 3: 2}
    got = set(zip(contracted["v"].to_list(), contracted["w"].to_list()))
    assert got == {(1, 2), (2, 1)}


def test_rc_compose_then_normalize():
    from goldenmatch.distributed.clustering import (
        _rc_compose_labels,
        _rc_normalize_to_min_member,
    )
    label = pl.DataFrame({"orig_id": [1, 2, 3], "cur": [1, 2, 3]})
    rep1 = pl.DataFrame({"v": [1, 2, 3], "rep": [1, 1, 2]})
    label = _rc_compose_labels(label, rep1)
    rep2 = pl.DataFrame({"v": [1, 2], "rep": [1, 1]})
    label = _rc_compose_labels(label, rep2)
    out = _rc_normalize_to_min_member(label)
    got = dict(zip(out["id"].to_list(), out["label"].to_list()))
    assert got == {1: 1, 2: 1, 3: 1}


def _partitions(out_pl):
    """Group {id,label} output into a sorted list of member tuples."""
    by_label = {}
    for i, lab in zip(out_pl["id"].to_list(), out_pl["label"].to_list()):
        by_label.setdefault(lab, set()).add(i)
    return sorted(tuple(sorted(s)) for s in by_label.values())


@pytest.mark.parametrize("seed", [0, 1, 7, 42])
def test_rc_wcc_polars_handtrace_shapes(seed):
    from goldenmatch.distributed.clustering import _rc_wcc_polars
    cases = {
        "chain":   ([(1, 2), (2, 3), (3, 4), (4, 5)], [(1, 2, 3, 4, 5)]),
        "star":    ([(1, 2), (1, 3), (1, 4)],         [(1, 2, 3, 4)]),
        "cycle":   ([(1, 2), (2, 3), (3, 1)],         [(1, 2, 3)]),
        "two_cc":  ([(1, 2), (3, 4)],                 [(1, 2), (3, 4)]),
        "dup":     ([(1, 2), (1, 2), (2, 1)],         [(1, 2)]),
    }
    for name, (pairs, expected) in cases.items():
        df = pl.DataFrame({"id_a": [a for a, _ in pairs], "id_b": [b for _, b in pairs]})
        out = _rc_wcc_polars(df, seed=seed)
        assert _partitions(out) == sorted(expected), f"{name} seed={seed}"
        for i, lab in zip(out["id"].to_list(), out["label"].to_list()):
            assert lab <= i


def test_rc_wcc_polars_long_chain_min_label():
    from goldenmatch.distributed.clustering import _rc_wcc_polars
    pairs = [(i, i + 1) for i in range(1, 200)]
    df = pl.DataFrame({"id_a": [a for a, _ in pairs], "id_b": [b for _, b in pairs]})
    out = _rc_wcc_polars(df, seed=3)
    assert _partitions(out) == [tuple(range(1, 201))]
    assert set(out["label"].to_list()) == {1}


def _scipy_partitions(n_nodes, pairs):
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    ids = sorted({x for e in pairs for x in e})
    idx = {v: i for i, v in enumerate(ids)}
    rows = [idx[a] for a, _ in pairs]
    cols = [idx[b] for _, b in pairs]
    data = np.ones(len(rows), dtype=np.int8)
    g = csr_matrix((data, (rows, cols)), shape=(len(ids), len(ids)))
    _ncc, labels = connected_components(g, directed=False)
    by = {}
    for v, lab in zip(ids, labels):
        by.setdefault(int(lab), set()).add(v)
    return sorted(tuple(sorted(s)) for s in by.values())


@pytest.mark.parametrize("trial", range(25))
def test_rc_wcc_polars_matches_scipy_on_random_graphs(trial):
    import random

    from goldenmatch.distributed.clustering import _rc_wcc_polars
    rng = random.Random(1000 + trial)
    n = rng.randint(2, 60)
    nodes = list(range(1, n + 1))
    n_edges = rng.randint(0, n * 2)
    pairs = []
    for _ in range(n_edges):
        a, b = rng.choice(nodes), rng.choice(nodes)
        if a != b:
            pairs.append((a, b))
    if not pairs:
        pytest.skip("no edges")
    df = pl.DataFrame({"id_a": [a for a, _ in pairs], "id_b": [b for _, b in pairs]})
    out = _rc_wcc_polars(df, seed=trial)
    assert _partitions(out) == _scipy_partitions(n, pairs)


def test_rc_wcc_chain_converges_in_log_rounds(monkeypatch):
    import goldenmatch.distributed.clustering as C
    rounds = {"n": 0}
    orig = C._rc_contract_round

    def _counting(edges_pl, A, B, p=C._RC_PRIME):
        rounds["n"] += 1
        return orig(edges_pl, A, B, p)

    monkeypatch.setattr(C, "_rc_contract_round", _counting)
    pairs = [(i, i + 1) for i in range(1, 1024)]
    df = pl.DataFrame({"id_a": [a for a, _ in pairs], "id_b": [b for _, b in pairs]})
    C._rc_wcc_polars(df, seed=5)
    assert rounds["n"] < 60, rounds["n"]


# ---------------------------------------------------------------------------
# No-Ray unit tests for the per-batch helper closures (run locally).
# ---------------------------------------------------------------------------

def test_rc_symmetrize_batch_roundtrip():
    import pyarrow as pa
    from goldenmatch.distributed.clustering import _rc_symmetrize_batch
    batch = pa.table({"id_a": [1, 2], "id_b": [2, 3]})
    out = pl.from_arrow(_rc_symmetrize_batch(batch))
    assert set(zip(out["v"].to_list(), out["w"].to_list())) == {(1, 2), (2, 1), (2, 3), (3, 2)}


def test_rc_rep_batch_picks_min_hash_neighbor():
    import pyarrow as pa
    from goldenmatch.distributed.clustering import _rc_rep_batch
    # symmetrized chain 1-2-3, identity hash A=1,B=0 -> rep {1:1,2:1,3:2}
    batch = pa.table({"v": [1, 2, 2, 3], "w": [2, 1, 3, 2]})
    out = pl.from_arrow(_rc_rep_batch(batch, 1, 0, 2**31 - 1))
    assert dict(zip(out["v"].to_list(), out["rep"].to_list())) == {1: 1, 2: 1, 3: 2}


# ---------------------------------------------------------------------------
# Ray-gated orchestration tests (function-level importorskip).
# ---------------------------------------------------------------------------

def test_randomized_contraction_wcc_matches_polars(tmp_path):
    pytest.importorskip("ray")
    from goldenmatch.distributed.clustering import (
        _rc_wcc_polars,
        pairs_list_to_dataset,
        randomized_contraction_wcc,
    )
    pairs = [(1, 2, 0.9), (2, 3, 0.8), (3, 4, 0.8), (10, 11, 0.7)]
    df = pl.DataFrame({"id_a": [a for a, _, _ in pairs], "id_b": [b for _, b, _ in pairs]})
    expected = _partitions(_rc_wcc_polars(df, seed=2))
    ds = pairs_list_to_dataset(pairs)
    out = randomized_contraction_wcc(ds, scratch_dir=str(tmp_path), seed=2)
    got = {}
    for r in out.take_all():
        got.setdefault(r["label"], set()).add(r["id"])
    assert sorted(tuple(sorted(s)) for s in got.values()) == expected


def test_randomized_contraction_wcc_chain(tmp_path):
    pytest.importorskip("ray")
    from goldenmatch.distributed.clustering import (
        pairs_list_to_dataset,
        randomized_contraction_wcc,
    )
    pairs = [(i, i + 1, 0.9) for i in range(1, 50)]
    ds = pairs_list_to_dataset(pairs)
    out = randomized_contraction_wcc(ds, scratch_dir=str(tmp_path), seed=1)
    labels = {r["id"]: r["label"] for r in out.take_all()}
    assert set(labels.values()) == {1}
    assert set(labels.keys()) == set(range(1, 51))


def test_randomized_contraction_wcc_keeps_caller_scratch(tmp_path):
    pytest.importorskip("ray")
    from goldenmatch.distributed.clustering import (
        pairs_list_to_dataset,
        randomized_contraction_wcc,
    )
    sub = tmp_path / "rc"
    ds = pairs_list_to_dataset([(1, 2, 0.9), (2, 3, 0.8)])
    randomized_contraction_wcc(ds, scratch_dir=str(sub), seed=0).take_all()
    # A caller-provided scratch dir is NOT auto-removed (owns_scratch=False), and
    # checkpointing actually happened -> at least one round file was written.
    assert sub.exists()
    assert any(sub.glob("**/*.parquet")), "expected round checkpoints under scratch"
