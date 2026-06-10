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
