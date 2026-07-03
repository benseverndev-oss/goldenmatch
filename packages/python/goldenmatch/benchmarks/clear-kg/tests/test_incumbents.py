"""The incumbent ER mechanisms: each is surface-only, so each merges homographs."""
from incumbents import predict_name_cosine, predict_neo4j_exact, predict_neo4j_fuzzy


def _clusters_of(mid, clusters):
    for c in clusters:
        if mid in c:
            return frozenset(c)
    return frozenset()


_MENTIONS = [
    {"mention_id": "a", "surface": "J. Smith"},   # entity X
    {"mention_id": "b", "surface": "J. Smith"},   # entity Y (homograph of a)
    {"mention_id": "c", "surface": "Wei Chen"},
]


def test_neo4j_exact_merges_identical_surfaces():
    cl = predict_neo4j_exact(_MENTIONS)
    # a and b share the identical surface -> merged (over-merge, the homograph bug)
    assert _clusters_of("a", cl) == _clusters_of("b", cl)


def test_neo4j_fuzzy_merges_identical_surfaces():
    cl = predict_neo4j_fuzzy(_MENTIONS)
    assert _clusters_of("a", cl) == _clusters_of("b", cl)


def test_name_cosine_merges_identical_surfaces():
    cl = predict_name_cosine(_MENTIONS)
    assert _clusters_of("a", cl) == _clusters_of("b", cl)


def test_fuzzy_merges_near_but_not_far_variants():
    # "john smith" ~ "j smith" merge; "amir khan" stays apart
    ms = [
        {"mention_id": "a", "surface": "John Smith"},
        {"mention_id": "b", "surface": "Jon Smith"},
        {"mention_id": "c", "surface": "Amir Khan"},
    ]
    cl = predict_neo4j_fuzzy(ms, ratio=0.85)
    assert _clusters_of("a", cl) == _clusters_of("b", cl)
    assert _clusters_of("c", cl) != _clusters_of("a", cl)
