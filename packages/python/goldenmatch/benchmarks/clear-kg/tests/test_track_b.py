"""End-to-end Track B: goldenmatch neighborhood ER beats EVERY documented
incumbent ER mechanism on the homograph split-rate AND on pairwise-F1. The
Phase-0 thesis, pinned."""
from generate import generate_corpus
from run_track_b import DEFAULT_ENGINES, run


def test_goldenmatch_beats_all_incumbents_on_both_axes():
    corpus = generate_corpus(seed=0, n_entities=20, n_homograph_pairs=5, docs_per_entity=3)
    res = run(corpus)
    gm = res["goldenmatch"]
    incumbents = {k: v for k, v in res.items() if k != "goldenmatch"}
    assert set(incumbents) == {"neo4j_exact", "neo4j_fuzzy", "name_cosine"}

    # every `if similar: merge` mechanism collapses on homographs...
    for name, s in incumbents.items():
        assert s["homograph_split_rate"] < 0.1, (name, s)
    # ...while neighborhood ER keeps them apart
    assert gm["homograph_split_rate"] > 0.9, gm

    # and goldenmatch is not paying for it -- it wins pairwise-F1 vs each incumbent
    for name, s in incumbents.items():
        assert gm["pairwise_f1"] > s["pairwise_f1"], (name, gm, s)


def test_default_engines_cover_the_documented_families():
    # exact-string, fuzzy-string, embedding-cosine-on-name, + the moat
    assert DEFAULT_ENGINES == ("neo4j_exact", "neo4j_fuzzy", "name_cosine", "goldenmatch")
