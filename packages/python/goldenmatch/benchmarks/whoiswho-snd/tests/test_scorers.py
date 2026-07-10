"""The set-Jaccard plugin scorer: pair contract + vectorized parity."""
import numpy as np
from normalize import encode_set
from scorers import SetJaccardScorer


def test_score_pair_matches_hand_jaccard():
    s = SetJaccardScorer()
    a = encode_set(["alice", "bob", "carol"])
    b = encode_set(["bob", "carol", "dave"])
    # inter {bob,carol}=2, union {a,b,c,d}=4 -> 0.5
    assert abs(s.score_pair(a, b) - 0.5) < 1e-9


def test_score_pair_none_and_empty():
    s = SetJaccardScorer()
    assert s.score_pair(None, "a") is None
    assert s.score_pair("a", None) is None
    assert s.score_pair("", "alice") == 0.0  # empty share nothing


def test_score_matrix_agrees_with_score_pair():
    s = SetJaccardScorer()
    vals = [
        encode_set(["alice", "bob"]),
        encode_set(["bob", "carol"]),
        encode_set(["dave"]),
        "",  # empty set row
    ]
    m = s.score_matrix(vals)
    assert m.shape == (4, 4)
    # symmetric + diagonal is self-Jaccard (1.0 for non-empty, 0.0 for empty)
    assert np.allclose(m, m.T)
    assert m[0, 0] == 1.0 and m[3, 3] == 0.0
    for i in range(len(vals)):
        for j in range(len(vals)):
            pair = s.score_pair(vals[i] or "", vals[j] or "")
            assert abs(m[i, j] - pair) < 1e-6


def test_score_matrix_all_empty_is_zero():
    s = SetJaccardScorer()
    m = s.score_matrix(["", "", None])
    assert m.shape == (3, 3)
    assert np.all(m == 0.0)


def test_registers_into_registry():
    import scorers
    from goldenmatch.plugins.registry import PluginRegistry

    scorers.register(force=True)
    assert PluginRegistry.instance().has_scorer("set_jaccard")
    assert PluginRegistry.instance().has_scorer("tfidf_cosine")


# --- TF-IDF cosine topical scorer (the embedding-fusion bridge) ---


def test_tfidf_close_topics_score_high_far_topics_low():
    from scorers import TfidfCosineScorer

    s = TfidfCosineScorer()
    vals = [
        "deep learning neural network image recognition",
        "neural network deep learning image classification",  # same topic as #0
        "soil carbon arctic permafrost tundra flux",          # unrelated
    ]
    m = s.score_matrix(vals)
    assert m.shape == (3, 3)
    assert m[0, 1] > 0.4          # shared domain vocabulary -> high cosine
    assert m[0, 2] < 0.1          # disjoint vocabulary -> ~0
    import numpy as np
    assert np.allclose(m, m.T)    # symmetric
    assert m[0, 0] > 0.99         # self-similarity ~1


def test_tfidf_empty_text_is_zero_and_safe():
    from scorers import TfidfCosineScorer

    s = TfidfCosineScorer()
    m = s.score_matrix(["", None, ""])
    assert m.shape == (3, 3)
    assert (m == 0.0).all()
    assert s.score_pair(None, "x") is None


def test_tfidf_score_pair_matches_matrix():
    from scorers import TfidfCosineScorer

    s = TfidfCosineScorer()
    a, b = "alpha beta gamma", "beta gamma delta"
    assert abs(s.score_pair(a, b) - s.score_matrix([a, b])[0, 1]) < 1e-6
