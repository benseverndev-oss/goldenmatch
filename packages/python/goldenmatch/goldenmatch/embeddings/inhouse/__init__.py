"""In-house, tailor-made embedding model for entity resolution.

A local, deterministic, CPU-only embedder built for ER:

    char n-gram feature hashing  ->  learned linear projection  ->  L2-normalize

The projection is trained on labeled match/non-match pairs with a numpy
contrastive loop (no torch) and exported to ONNX for portable, language-agnostic
inference (served via onnxruntime; the basis for the ``goldenembed-rs`` runtime).

    from goldenmatch.embeddings.inhouse import train_embedder, TrainConfig
    from goldenmatch.embeddings import embed_records

    model, report = train_embedder(labeled_pairs)
    model.save("my_model")                 # writes weights + config + model.onnx
    vecs = embed_records(texts, provider="inhouse", model="my_model")
"""
from __future__ import annotations

from goldenmatch.embeddings.inhouse.featurizer import (
    CharNGramFeaturizer,
    FeaturizerConfig,
)
from goldenmatch.embeddings.inhouse.model import EmbedModelConfig, GoldenEmbedModel
from goldenmatch.embeddings.inhouse.trainer import (
    TrainConfig,
    TrainReport,
    train_embedder,
)

__all__ = [
    "CharNGramFeaturizer",
    "EmbedModelConfig",
    "FeaturizerConfig",
    "GoldenEmbedModel",
    "TrainConfig",
    "TrainReport",
    "train_embedder",
]
