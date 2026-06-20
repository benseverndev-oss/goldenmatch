"""Tests for score_sketch_pairs sketch-distance verifier (#1083)."""
import numpy as np
from goldenmatch.core.throughput_verify import score_sketch_pairs


def test_jaccard_keeps_only_above_threshold():
    texts = ["the quick brown fox", "the quick brown fox", "a totally different string here"]
    pairs = {(0, 1), (0, 2)}
    out = score_sketch_pairs(pairs, metric="jaccard", threshold=0.8, texts=texts,
                             mode="word", k=2, num_perms=128, seed=0)
    ids = {(a, b) for a, b, _ in out}
    assert (0, 1) in ids
    assert (0, 2) not in ids
    assert all(0.0 <= sc <= 1.0 for _, _, sc in out)


def test_cosine_uses_supplied_embeddings():
    emb = np.array([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    pairs = {(0, 1), (0, 2)}
    out = score_sketch_pairs(pairs, metric="cosine", threshold=0.85, embeddings=emb)
    ids = {(a, b) for a, b, _ in out}
    assert (0, 1) in ids and (0, 2) not in ids


def test_output_is_canonical_min_max_triples():
    texts = ["aa", "aa"]
    out = score_sketch_pairs({(1, 0)}, metric="jaccard", threshold=0.1, texts=texts,
                             mode="char", k=1, num_perms=64, seed=0)
    assert out and out[0][0] < out[0][1]
