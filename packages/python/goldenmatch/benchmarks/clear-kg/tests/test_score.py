"""Track B scoring: pairwise, B-cubed, and the homograph split-rate."""
from score import bcubed_prf, homograph_split_rate, pairwise_prf


def test_pairwise_perfect_and_over_merge():
    gold = {"a": "E1", "b": "E1", "c": "E2"}
    assert pairwise_prf([["a", "b"], ["c"]], gold)["f1"] == 1.0
    # merge everything: c wrongly joined -> precision drop, recall 1
    s = pairwise_prf([["a", "b", "c"]], gold)
    assert s["recall"] == 1.0 and s["precision"] < 1.0


def test_bcubed_reasonable():
    gold = {"a": "E1", "b": "E1", "c": "E2"}
    assert bcubed_prf([["a", "b"], ["c"]], gold)["f1"] == 1.0
    assert bcubed_prf([["a", "b", "c"]], gold)["recall"] == 1.0


def test_homograph_split_rate_counts_only_shared_surface_different_entity():
    # two "j smith" mentions of DIFFERENT entities (confusable) + one unique
    mentions = [
        {"mention_id": "a", "surface": "J. Smith"},
        {"mention_id": "b", "surface": "J. Smith"},
        {"mention_id": "c", "surface": "Wei Chen"},
    ]
    gold = {"a": "E1", "b": "E2", "c": "E3"}
    # merged a,b -> NOT split -> rate 0
    merged = homograph_split_rate([["a", "b"], ["c"]], gold, mentions)
    assert merged["confusable_pairs"] == 1 and merged["split_rate"] == 0.0
    # separated a,b -> split -> rate 1
    split = homograph_split_rate([["a"], ["b"], ["c"]], gold, mentions)
    assert split["confusable_pairs"] == 1 and split["split_rate"] == 1.0


def test_split_rate_ignores_same_entity_and_different_surface():
    mentions = [
        {"mention_id": "a", "surface": "J. Smith"},   # E1
        {"mention_id": "b", "surface": "John Smith"},  # E1 (same entity, diff surface)
        {"mention_id": "c", "surface": "Ann Lee"},     # E2 (diff surface)
    ]
    gold = {"a": "E1", "b": "E1", "c": "E2"}
    # no two mentions share a surface AND differ in entity -> no confusable pairs
    assert homograph_split_rate([["a", "b", "c"]], gold, mentions)["confusable_pairs"] == 0
