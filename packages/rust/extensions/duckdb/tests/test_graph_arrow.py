"""Value-level tests for the native-direct columnar graph UDFs.

``goldenmatch_pair_dedup`` / ``goldenmatch_connected_components`` (int64 ids)
and their ``_str`` siblings (string ids) take the WHOLE candidate-pair / edge
columns as DuckDB ``LIST`` arguments (callers aggregate with ``list(col)``) and
return ``LIST`` results -- a list of ``STRUCT`` for pair_dedup, a list of lists
for connected_components. No JSON wire. They call the native kernel directly.
"""
from __future__ import annotations

import duckdb
import pytest
from goldenmatch_duckdb.functions import register


@pytest.fixture()
def con():
    c = duckdb.connect()
    register(c)
    return c


class TestPairDedupColumnar:
    def test_canonicalizes_and_keeps_max_int(self, con):
        # pairs (2,1,0.5),(1,2,0.9) -> canonical (1,2) with max 0.9, not (2,1)/0.5
        rows = con.execute(
            "SELECT goldenmatch_pair_dedup([2, 1], [1, 2], [0.5, 0.9])"
        ).fetchone()[0]
        # list of STRUCT(a,b,s)
        pairs = {(r["a"], r["b"], r["s"]) for r in rows}
        assert (1, 2, 0.9) in pairs
        assert (2, 1, 0.5) not in pairs
        assert all(r["s"] != 0.5 for r in rows)

    def test_str_ids(self, con):
        rows = con.execute(
            "SELECT goldenmatch_pair_dedup_str(['b', 'a'], ['a', 'b'], [0.5, 0.9])"
        ).fetchone()[0]
        # canonical order preserved on the string ids (first-seen -> 'b','a')
        pairs = {(r["a"], r["b"], r["s"]) for r in rows}
        assert len(rows) == 1
        (a, b, s), = pairs
        assert {a, b} == {"a", "b"}
        assert s == 0.9


class TestConnectedComponentsColumnar:
    def test_groups_and_singleton_int(self, con):
        # edges (1,2,0.9),(2,3,0.8) ; universe {1,2,3,4} -> {{1,2,3},{4}}
        comps = con.execute(
            "SELECT goldenmatch_connected_components("
            "[1, 2], [2, 3], [0.9, 0.8], [1, 2, 3, 4])"
        ).fetchone()[0]
        got = {frozenset(c) for c in comps}
        assert got == {frozenset({1, 2, 3}), frozenset({4})}

    def test_groups_and_singleton_str(self, con):
        # edges (x,y,0.9),(y,z,0.8) ; universe {x,y,z,w} -> {{x,y,z},{w}}
        comps = con.execute(
            "SELECT goldenmatch_connected_components_str("
            "['x', 'y'], ['y', 'z'], [0.9, 0.8], ['x', 'y', 'z', 'w'])"
        ).fetchone()[0]
        got = {frozenset(c) for c in comps}
        assert got == {frozenset({"x", "y", "z"}), frozenset({"w"})}

    def test_matches_native_reference_int(self, con):
        from goldenmatch.native import connected_components

        comps = con.execute(
            "SELECT goldenmatch_connected_components("
            "[10, 11], [11, 12], [0.9, 0.8], [10, 11, 12, 99])"
        ).fetchone()[0]
        ref = connected_components(
            [(10, 11, 0.9), (11, 12, 0.8)], [10, 11, 12, 99]
        )
        assert {frozenset(c) for c in comps} == {frozenset(c) for c in ref}
