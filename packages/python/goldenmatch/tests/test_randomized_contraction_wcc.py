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
        _rc_compose_labels, _rc_normalize_to_min_member,
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
