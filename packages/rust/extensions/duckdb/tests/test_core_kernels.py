"""Tests for the local-embedding UDF in ``core_kernels.py``.

The graph UDFs moved to a native-direct columnar shape -- their tests live in
``test_graph_arrow.py``. This module covers the JSON ``goldenmatch_embed_local``
UDF (owned by the embed task), which stays JSON in / JSON out.
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


class TestEmbedLocal:
    def test_embeds_with_saved_model(self, con, tmp_path):
        # goldenmatch_embed_local now runs the goldenembed-rs ONNX kernel via the
        # optional `goldenmatch-embed` wheel (an extra), which loads `model.onnx`.
        # That export is only written by `model.save` when `onnx` is installed.
        # So this happy-path needs BOTH; skip where either is absent (e.g. the
        # duckdb_extensions lane, which doesn't install the wheel). The wheel's
        # embed correctness is covered directly in the `embed_wheel` CI lane.
        import pytest

        pytest.importorskip("goldenmatch_embed")
        pytest.importorskip("onnx")
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
