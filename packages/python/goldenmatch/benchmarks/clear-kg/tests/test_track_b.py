"""End-to-end Track B: goldenmatch neighborhood ER beats exact-surface on the
homograph split-rate AND on pairwise-F1. The Phase-0 thesis, pinned."""
from generate import generate_corpus
from run_track_b import run


def test_goldenmatch_beats_exact_surface_on_both_axes():
    corpus = generate_corpus(seed=0, n_entities=20, n_homograph_pairs=5, docs_per_entity=3)
    res = run(corpus)
    ex, gm = res["exact_surface"], res["goldenmatch"]

    # the money metric: exact-surface merges every homograph (~0); goldenmatch splits them
    assert ex["homograph_split_rate"] < 0.1, ex
    assert gm["homograph_split_rate"] > 0.9, gm

    # and goldenmatch is not paying for it with recall -- it wins pairwise-F1 too
    assert gm["pairwise_f1"] > ex["pairwise_f1"], (gm, ex)


def test_exact_surface_over_merges_homographs_to_zero():
    corpus = generate_corpus(seed=3, n_homograph_pairs=6)
    res = run(corpus, engines=("exact_surface",))
    assert res["exact_surface"]["homograph_split_rate"] == 0.0
