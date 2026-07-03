"""Real-data (Wikipedia) validity track -- exercised OFFLINE with a tiny
hand-built `articles` dict so CI never touches the network. `build_mentions` is
pure; `load_corpus(offline_articles=...)` skips the fetch. The live fetch is
covered by `run_real.py` (manual / network-gated), not here."""
from real_data import build_mentions, load_corpus
from run_track_b import run

# Two ambiguous surfaces, each mapping to two real-shaped articles with DISJOINT
# neighborhoods -- the homograph condition, hand-authored so it's offline.
_GROUPS = [
    {"surface": "Mercury", "entities": ["Mercury (planet)", "Mercury (element)"]},
    {"surface": "Java", "entities": ["Java (programming language)", "Java"]},
]
_PARA = "x" * 100  # >= min_chunk_len so paragraph splitting yields chunks
_ARTICLES = {
    "Mercury (planet)": {
        "title": "Mercury (planet)",
        "extract": f"Mercury is the first planet.\n{_PARA}\n{_PARA}",
        "links": ["Solar System", "Sun", "Orbit", "Venus"],
    },
    "Mercury (element)": {
        "title": "Mercury (element)",
        "extract": f"Mercury is a chemical element.\n{_PARA}\n{_PARA}",
        "links": ["Chemical element", "Metal", "Thermometer", "Toxicity"],
    },
    "Java (programming language)": {
        "title": "Java (programming language)",
        "extract": f"Java is a programming language.\n{_PARA}\n{_PARA}",
        "links": ["Object-oriented programming", "JVM", "Oracle", "Bytecode"],
    },
    "Java": {
        "title": "Java",
        "extract": f"Java is an island in Indonesia.\n{_PARA}\n{_PARA}",
        "links": ["Indonesia", "Jakarta", "Island", "Volcano"],
    },
}


def test_build_mentions_shape():
    ms = build_mentions(_ARTICLES, _GROUPS, max_mentions_per_entity=3, top_k_neighbors=4)
    # every entity gets >= 1 mention; surface is the shared ambiguous string
    mercury = [m for m in ms if m["gold_entity_id"] == "Mercury (planet)"]
    assert mercury, "planet entity produced no mentions"
    assert all(m["surface"] == "Mercury" for m in mercury)
    # gold is the (distinct) article title, neighbors are the article's links
    assert set(mercury[0]["neighbor_surfaces"]) == {"Solar System", "Sun", "Orbit", "Venus"}
    # the two Mercury articles are surface-confusable but gold-distinct
    golds = {m["gold_entity_id"] for m in ms if m["surface"] == "Mercury"}
    assert golds == {"Mercury (planet)", "Mercury (element)"}


def test_offline_corpus_has_real_homograph_pairs():
    corpus = load_corpus(offline_articles=_ARTICLES, groups=_GROUPS,
                         max_mentions_per_entity=3, top_k_neighbors=4)
    res = run(corpus)
    gm = res["goldenmatch"]
    # there ARE confusable pairs to separate (else the metric is vacuous)
    assert gm["homograph_confusable"] > 0


def test_goldenmatch_holds_the_moat_offline():
    corpus = load_corpus(offline_articles=_ARTICLES, groups=_GROUPS,
                         max_mentions_per_entity=3, top_k_neighbors=4)
    res = run(corpus)
    gm = res["goldenmatch"]
    incumbents = {k: v for k, v in res.items() if k != "goldenmatch"}

    # every `if similar: merge` mechanism collapses on real-shaped homographs...
    for name, s in incumbents.items():
        assert s["homograph_split_rate"] < 0.1, (name, s)
    # ...while neighborhood ER keeps the disjoint-neighborhood articles apart
    assert gm["homograph_split_rate"] > 0.9, gm
    # and recovers more entities than the surface-merging incumbents
    for name, s in incumbents.items():
        assert gm["n_pred_clusters"] > s["n_pred_clusters"], (name, gm, s)
