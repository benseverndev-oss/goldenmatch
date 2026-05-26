"""Tests for the in-house ER embedder (char n-gram -> learned projection -> ONNX).

The numpy forward pass is the source of truth. ONNX-dependent tests skip when
onnx/onnxruntime aren't installed; everything else runs with numpy alone.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.embeddings import embed_records
from goldenmatch.embeddings.inhouse import (
    CharNGramFeaturizer,
    EmbedModelConfig,
    FeaturizerConfig,
    GoldenEmbedModel,
    TrainConfig,
    train_embedder,
)

_HAS_ONNX = True
try:
    import onnx  # noqa: F401
    import onnxruntime  # noqa: F401
except ImportError:
    _HAS_ONNX = False

onnx_required = pytest.mark.skipif(not _HAS_ONNX, reason="onnx/onnxruntime not installed")


# ----- featurizer -----

def test_featurizer_deterministic_and_normalized():
    f = CharNGramFeaturizer(FeaturizerConfig(n_features=512))
    a = f.transform(["Acme Corp"])
    b = f.transform(["Acme Corp"])
    assert np.array_equal(a, b)
    assert a.shape == (1, 512)
    assert np.linalg.norm(a[0]) == pytest.approx(1.0, abs=1e-6)


def test_featurizer_lowercase_and_whitespace_collapse():
    f = CharNGramFeaturizer(FeaturizerConfig(n_features=512))
    assert np.array_equal(f.transform(["Acme  Corp"]), f.transform(["acme corp"]))


def test_featurizer_similar_strings_closer_than_dissimilar():
    f = CharNGramFeaturizer(FeaturizerConfig(n_features=4096))
    v = f.transform(["John Smith", "Jon Smith", "Zebra Industries"])
    sim_close = float(v[0] @ v[1])
    sim_far = float(v[0] @ v[2])
    assert sim_close > sim_far


def test_featurizer_empty_text_is_zero_vector():
    f = CharNGramFeaturizer(FeaturizerConfig(n_features=128))
    assert not f.transform([""]).any()


def test_featurizer_seed_changes_hashing():
    cfg_a = FeaturizerConfig(n_features=512, seed=0)
    cfg_b = FeaturizerConfig(n_features=512, seed=1)
    va = CharNGramFeaturizer(cfg_a).transform(["hello world"])
    vb = CharNGramFeaturizer(cfg_b).transform(["hello world"])
    assert not np.array_equal(va, vb)


# ----- model forward -----

def test_model_embed_shape_and_norm():
    m = GoldenEmbedModel(
        EmbedModelConfig(dim=32, featurizer=FeaturizerConfig(n_features=1024))
    )
    out = m.embed(["alpha", "beta", "gamma"], backend="numpy")
    assert out.shape == (3, 32)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)


def test_model_embed_empty():
    m = GoldenEmbedModel(EmbedModelConfig(dim=16))
    assert m.embed([], backend="numpy").shape == (0, 16)


def test_untrained_model_preserves_lexical_similarity():
    # random projection should still keep similar strings closer than dissimilar
    m = GoldenEmbedModel(
        EmbedModelConfig(dim=64, featurizer=FeaturizerConfig(n_features=4096)), seed=7
    )
    v = m.embed(["Robert Jones", "Bob Jones", "Margaret Chen"], backend="numpy")
    assert float(v[0] @ v[1]) > float(v[0] @ v[2])


def test_model_id_tracks_weights():
    m1 = GoldenEmbedModel(EmbedModelConfig(dim=16), seed=1)
    m2 = GoldenEmbedModel(EmbedModelConfig(dim=16), seed=2)
    assert m1.model_id != m2.model_id
    assert m1.model_id == GoldenEmbedModel(EmbedModelConfig(dim=16), seed=1).model_id


# ----- ONNX export parity -----

@onnx_required
def test_onnx_matches_numpy_forward():
    m = GoldenEmbedModel(
        EmbedModelConfig(dim=48, featurizer=FeaturizerConfig(n_features=1024)), seed=3
    )
    texts = ["John Smith", "Jonathan Smyth", "", "ACME, Inc."]
    np_out = m.embed(texts, backend="numpy")
    onnx_out = m.embed(texts, backend="onnx")
    assert np.allclose(np_out, onnx_out, atol=1e-5)


@onnx_required
def test_onnx_with_bias_matches_numpy():
    m = GoldenEmbedModel(EmbedModelConfig(dim=24, use_bias=True))
    m.bias = np.linspace(-0.5, 0.5, 24).astype(np.float32)
    texts = ["foo bar", "baz qux"]
    assert np.allclose(
        m.embed(texts, backend="numpy"), m.embed(texts, backend="onnx"), atol=1e-5
    )


@onnx_required
def test_save_writes_onnx_and_load_roundtrips(tmp_path):
    m = GoldenEmbedModel(EmbedModelConfig(dim=32), seed=5)
    m.save(tmp_path / "mdl")
    assert (tmp_path / "mdl" / "model.onnx").exists()
    loaded = GoldenEmbedModel.load(tmp_path / "mdl")
    assert loaded.model_id == m.model_id
    texts = ["a b c", "d e f"]
    assert np.array_equal(
        loaded.embed(texts, backend="numpy"), m.embed(texts, backend="numpy")
    )


# ----- training -----

def _toy_pairs():
    matches = [
        ("John Smith", "Jon Smith", 1),
        ("Robert Jones", "Bob Jones", 1),
        ("Acme Corporation", "Acme Corp", 1),
        ("Margaret Chen", "Maggie Chen", 1),
        ("International Business", "Internationl Business", 1),
    ]
    non = [
        ("John Smith", "Margaret Chen", 0),
        ("Acme Corporation", "Zebra Industries", 0),
        ("Robert Jones", "Acme Corp", 0),
        ("Maggie Chen", "Zebra Industries", 0),
        ("Jon Smith", "Bob Jones", 0),
    ]
    return matches + non


def test_training_is_deterministic():
    pairs = _toy_pairs()
    cfg = TrainConfig(dim=32, epochs=30, seed=0, featurizer=FeaturizerConfig(n_features=1024))
    m1, _ = train_embedder(pairs, cfg)
    m2, _ = train_embedder(pairs, cfg)
    assert np.array_equal(m1.weights, m2.weights)


def test_training_improves_separation():
    pairs = _toy_pairs()
    cfg = TrainConfig(dim=32, epochs=150, lr=0.5, seed=0,
                      featurizer=FeaturizerConfig(n_features=2048))
    _model, report = train_embedder(pairs, cfg)
    assert report.separation_after > report.separation_before
    # matches should end up clearly more similar than non-matches on average
    assert report.separation_after > 0.1


def test_train_requires_pairs():
    with pytest.raises(ValueError):
        train_embedder([])


def test_cosine_is_default_loss_and_improves_separation():
    cfg = TrainConfig(dim=32, epochs=150, lr=0.5, seed=0,
                      featurizer=FeaturizerConfig(n_features=2048))
    assert cfg.loss == "cosine"
    _model, report = train_embedder(_toy_pairs(), cfg)
    assert report.separation_after > report.separation_before
    assert report.separation_after > 0.1


def test_cosine_loss_is_deterministic():
    cfg = TrainConfig(dim=32, epochs=30, seed=0, loss="cosine",
                      featurizer=FeaturizerConfig(n_features=1024))
    m1, _ = train_embedder(_toy_pairs(), cfg)
    m2, _ = train_embedder(_toy_pairs(), cfg)
    assert np.array_equal(m1.weights, m2.weights)


def test_euclidean_loss_option_still_trains():
    cfg = TrainConfig(dim=32, epochs=150, lr=0.5, seed=0, loss="euclidean",
                      featurizer=FeaturizerConfig(n_features=2048))
    _model, report = train_embedder(_toy_pairs(), cfg)
    assert report.separation_after > report.separation_before


def test_unknown_loss_raises():
    with pytest.raises(ValueError, match="loss"):
        train_embedder(_toy_pairs(), TrainConfig(loss="triplet"))


@onnx_required
def test_cosine_trained_model_still_exports_to_onnx_and_matches():
    # The loss changes the weights, not the graph: the ONNX projection head must
    # still match the numpy forward exactly after cosine training.
    model, _ = train_embedder(
        _toy_pairs(), TrainConfig(dim=16, epochs=20, seed=0, loss="cosine",
                                  featurizer=FeaturizerConfig(n_features=512))
    )
    texts = ["John Smith", "Acme Corp", "Margaret Chen"]
    feats = model.featurizer.transform(texts)
    np.testing.assert_allclose(model.project(feats), model._project_onnx(feats), atol=1e-5)


# ----- provider integration -----

def test_embed_records_inhouse_provider():
    model, _ = train_embedder(
        _toy_pairs(), TrainConfig(dim=16, epochs=20, seed=0,
                                  featurizer=FeaturizerConfig(n_features=512))
    )
    out = embed_records(["John Smith", "Jon Smith"], provider="inhouse", model=model)
    assert out.shape == (2, 16)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)


def test_embed_records_inhouse_from_saved_path(tmp_path):
    model, _ = train_embedder(
        _toy_pairs(), TrainConfig(dim=16, epochs=10, seed=0,
                                  featurizer=FeaturizerConfig(n_features=512))
    )
    model.save(tmp_path / "m")
    out = embed_records(["Acme Corp", "Acme Corporation"], provider="inhouse",
                        model=str(tmp_path / "m"))
    assert out.shape == (2, 16)


def test_inhouse_requires_model():
    from goldenmatch.embeddings import resolve_provider
    with pytest.raises(ValueError):
        resolve_provider("inhouse")
