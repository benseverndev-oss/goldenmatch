"""Homograph split-rate metric (the CLEAR-KG headline, ported onto the real-
framework board). Of same-surface / different-entity pairs, the fraction kept
apart."""
from erkgbench.metrics import homograph_split_rate

# two records share the surface "J. Smith" but are DIFFERENT entities (A, B);
# a third is unrelated.
_MENTIONS = ["J. Smith", "J. Smith", "Wei Chen"]
_ENTITY_IDS = ["A", "B", "C"]


def test_merging_the_homograph_scores_zero():
    # a resolver that clusters the two "J. Smith" together (the if-similar-merge bug)
    r = homograph_split_rate(_MENTIONS, _ENTITY_IDS, [[0, 1]])
    assert r.confusable == 1
    assert r.split == 0
    assert r.rate == 0.0


def test_keeping_the_homograph_apart_scores_one():
    # singletons (absent from every cluster) count as their own cluster
    r = homograph_split_rate(_MENTIONS, _ENTITY_IDS, [])
    assert r.confusable == 1 and r.split == 1
    assert r.rate == 1.0


def test_surface_normalization_and_same_entity_excluded():
    # casing/whitespace fold to one surface; same-entity pairs are NOT confusable
    mentions = ["Georgia", "georgia ", "Georgia"]
    entity_ids = ["Q1", "Q2", "Q1"]  # idx0/2 same entity; idx1 a homograph of them
    r = homograph_split_rate(mentions, entity_ids, [[0, 1, 2]])  # all merged
    # confusable pairs: (0,1) and (1,2) differ in entity -> 2; (0,2) same entity -> excluded
    assert r.confusable == 2
    assert r.split == 0  # all merged -> none kept apart


def test_no_homographs_is_vacuously_one():
    r = homograph_split_rate(["a", "b"], ["X", "Y"], [])
    assert r.confusable == 0 and r.rate == 1.0
