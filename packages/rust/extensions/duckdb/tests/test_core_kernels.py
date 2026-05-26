"""Tests for the Native Core + local-embedding UDFs in ``core_kernels.py``.

Registers the UDFs on a DuckDB connection and asserts the JSON results against
what the underlying goldenmatch functions return — real parity, not a tautology.
"""
from __future__ import annotations

import json

import duckdb
import pytest
from goldenmatch_duckdb.functions import register


@pytest.fixture()
def con():
    c = duckdb.connect()
    register(c)
    return c


def _scalar(con, sql: str) -> str:
    return con.sql(sql).fetchone()[0]


class TestConnectedComponents:
    def test_groups_and_singleton(self, con):
        out = _scalar(
            con,
            "SELECT goldenmatch_connected_components('[[1,2,0.9],[2,3,0.8],[10,11,0.7]]')",
        )
        comps = {frozenset(c) for c in json.loads(out)}
        assert comps == {frozenset({1, 2, 3}), frozenset({10, 11})}

    def test_bad_json_is_fail_soft(self, con):
        out = _scalar(con, "SELECT goldenmatch_connected_components('not json')")
        assert "error" in json.loads(out)


class TestPairDedup:
    def test_canonicalizes_and_keeps_max(self, con):
        out = _scalar(
            con,
            "SELECT goldenmatch_pair_dedup('[[2,1,0.5],[1,2,0.9],[3,4,0.2],[4,3,0.7]]')",
        )
        assert json.loads(out) == [[1, 2, 0.9], [3, 4, 0.7]]

    def test_matches_native_reference(self, con):
        from goldenmatch.native import dedup_pairs_max_score

        pairs = [[5, 1, 0.3], [1, 5, 0.8], [2, 2, 1.0]]
        out = json.loads(_scalar(con, f"SELECT goldenmatch_pair_dedup('{json.dumps(pairs)}')"))
        ref = [[a, b, s] for a, b, s in dedup_pairs_max_score(
            [(int(a), int(b), float(s)) for a, b, s in pairs])]
        assert out == ref


class TestEmbedLocal:
    def test_embeds_with_saved_model(self, con, tmp_path):
        from goldenmatch.embeddings.inhouse import (
            FeaturizerConfig,
            TrainConfig,
            train_embedder,
        )

        model, _ = train_embedder(
            [("John Smith", "Jon Smith", 1), ("Acme Corp", "Zebra Inc", 0)],
            TrainConfig(dim=16, epochs=10, seed=0,
                        featurizer=FeaturizerConfig(n_features=512)),
        )
        path = tmp_path / "m"
        model.save(path)
        out = json.loads(
            _scalar(con, f"SELECT goldenmatch_embed_local('John Smith', '{path}')")
        )
        assert isinstance(out, list)
        assert len(out) == 16

    def test_missing_model_is_fail_soft(self, con):
        out = _scalar(
            con, "SELECT goldenmatch_embed_local('x', '/nonexistent/model/dir')"
        )
        assert "error" in json.loads(out)
