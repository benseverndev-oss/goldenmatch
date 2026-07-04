"""Tests for the MinHash-LSH token-blocking UDF (``goldenmatch_lsh_pairs``).

Aggregate a text column with ``list(text)`` and get canonical candidate pairs
back. The UDF reuses the native-gated ``MinHashLSHBlocker``, so its output must
match that blocker exactly (the SQL surface is not a reimplementation).
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


def _pairs(con, texts, mode="char", k=3, num_perms=32, num_bands=8, seed=42):
    con.execute("CREATE TABLE t (id BIGINT, txt VARCHAR)")
    rows = [(i, t) for i, t in enumerate(texts)]
    if rows:
        con.executemany("INSERT INTO t VALUES (?, ?)", rows)
    res = con.sql(
        f"SELECT goldenmatch_lsh_pairs(list(txt), '{mode}', {k}, "
        f"{num_perms}, {num_bands}, {seed}) FROM t"
    ).fetchone()[0]
    return {(p["a"], p["b"]) for p in (res or [])}


class TestLshPairs:
    def test_empty(self, con):
        assert _pairs(con, []) == set()

    def test_finds_near_duplicate_pair(self, con):
        # Two near-identical strings + one clearly distinct — the dup pair must
        # surface; the distinct record must not pair with them.
        texts = ["Acme Corporation", "Acme Corporaton", "Zzyzx Widgets"]
        pairs = _pairs(con, texts, mode="char", k=3, num_perms=64, num_bands=16)
        assert (0, 1) in pairs
        assert (0, 2) not in pairs and (1, 2) not in pairs
        for a, b in pairs:
            assert a < b  # canonical

    def test_matches_python_blocker_exactly(self, con):
        # The SQL surface must reproduce the canonical MinHashLSHBlocker's
        # candidate set byte-for-byte (same sketch kernel underneath).
        from goldenmatch.core.lsh_blocker import MinHashLSHBlocker

        texts = [
            "Globex International",
            "Globex Internatonal",  # typo — near-dup of 0
            "Initech LLC",
            "Initech L.L.C.",
            "Umbrella Corp",
            "",  # empty: blocks on nothing
            "Stark Industries",
            "Stark Industies",  # typo — near-dup of 6
        ]
        got = _pairs(con, texts, mode="char", k=3, num_perms=64, num_bands=16, seed=7)
        blocker = MinHashLSHBlocker("char", 3, 64, 16, 7)
        want = blocker.candidate_pairs(texts)
        assert got == want
        assert len(want) > 0  # near-dup pairs collide, so the reroute is exercised
