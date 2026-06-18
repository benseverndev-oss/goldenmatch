"""Integration: the in-house embedder backing the ER embedding scorers.

Trains a small in-house model, then drives it through the same code path the
`embedding` / `record_embedding` scorers use (`get_embedder` ->
`embed_column` / `cosine_similarity_matrix`). No cloud, no torch.
"""
from __future__ import annotations

import pytest
from goldenmatch.embeddings.inhouse import FeaturizerConfig, TrainConfig, train_embedder


def _toy_pairs():
    matches = [
        ("John Smith", "Jon Smith", 1),
        ("Robert Jones", "Bob Jones", 1),
        ("Acme Corporation", "Acme Corp", 1),
        ("Margaret Chen", "Maggie Chen", 1),
        ("Elizabeth Warren", "Liz Warren", 1),
    ]
    non = [
        ("John Smith", "Margaret Chen", 0),
        ("Acme Corporation", "Zebra Industries", 0),
        ("Robert Jones", "Acme Corp", 0),
        ("Liz Warren", "Zebra Industries", 0),
        ("Jon Smith", "Bob Jones", 0),
    ]
    return matches + non


@pytest.fixture
def inhouse_model_path(tmp_path):
    model, _ = train_embedder(
        _toy_pairs(),
        TrainConfig(dim=32, epochs=120, lr=0.5, seed=0,
                    featurizer=FeaturizerConfig(n_features=2048)),
    )
    path = tmp_path / "model"
    model.save(path)
    return str(path)


def test_get_embedder_routes_to_inhouse(inhouse_model_path):
    from goldenmatch.core.embedder import _ProviderEmbedder, get_embedder

    emb = get_embedder(f"inhouse:{inhouse_model_path}")
    assert isinstance(emb, _ProviderEmbedder)
    vecs = emb.embed_column(["John Smith", "Jon Smith"], cache_key="k1")
    assert vecs.shape == (2, 32)
    sim = emb.cosine_similarity_matrix(vecs)
    assert sim.shape == (2, 2)
    assert sim[0, 1] == pytest.approx(sim[1, 0])  # symmetric
    assert -1.01 <= float(sim[0, 1]) <= 1.01


def test_embedding_scorer_uses_inhouse_model(inhouse_model_path):
    from goldenmatch.core.scorer import _fuzzy_score_matrix

    values = ["John Smith", "Jon Smith", "Zebra Industries"]
    matrix = _fuzzy_score_matrix(values, "embedding", model_name=f"inhouse:{inhouse_model_path}")
    assert matrix.shape == (3, 3)
    # similar names should score higher than dissimilar
    assert matrix[0, 1] > matrix[0, 2]


def test_inhouse_embedder_zero_config_default(monkeypatch):
    # Contract change: bare get_embedder("inhouse") (no path, no env) now returns the
    # untrained fixed-seed default GoldenEmbedModel (zero-config) instead of raising
    # ValueError. A trained model is still used when "inhouse:<path>"/the env is given.
    from goldenmatch.core.embedder import _embedders, get_embedder

    monkeypatch.delenv("GOLDENMATCH_INHOUSE_MODEL", raising=False)
    _embedders.pop("inhouse", None)
    emb = get_embedder("inhouse")
    assert emb is not None
