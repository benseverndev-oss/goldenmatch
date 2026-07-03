"""The synthetic generator: determinism, homograph injection, disjoint signatures."""
from er_utils import norm
from generate import generate_corpus


def test_deterministic_for_a_seed():
    a = generate_corpus(seed=7)
    b = generate_corpus(seed=7)
    assert [m["mention_id"] for m in a["mentions"]] == [m["mention_id"] for m in b["mentions"]]
    assert [m["surface"] for m in a["mentions"]] == [m["surface"] for m in b["mentions"]]


def test_homographs_map_one_surface_to_multiple_entities():
    c = generate_corpus(seed=0, n_homograph_pairs=5)
    assert c["homograph_surfaces"]
    gold = {m["mention_id"]: m["gold_entity_id"] for m in c["mentions"]}
    for hs in c["homograph_surfaces"]:
        ents = {gold[m["mention_id"]] for m in c["mentions"] if norm(m["surface"]) == norm(hs)}
        assert len(ents) >= 2, f"{hs!r} should be shared by >=2 entities, got {ents}"


def test_every_mention_has_gold_and_neighbors():
    c = generate_corpus(seed=1)
    for m in c["mentions"]:
        assert m["gold_entity_id"]
        assert "neighbor_surfaces" in m
    # gold entity ids all resolve to a real entity
    eids = {e["entity_id"] for e in c["entities"]}
    assert all(m["gold_entity_id"] in eids for m in c["mentions"])


def test_homograph_pairs_have_disjoint_neighbor_signatures():
    c = generate_corpus(seed=0)
    from er_utils import decode_set, encode_set
    gold = {m["mention_id"]: m["gold_entity_id"] for m in c["mentions"]}
    sig = {m["gold_entity_id"]: encode_set(m["neighbor_surfaces"]) for m in c["mentions"]}
    for hs in c["homograph_surfaces"]:
        ents = sorted({gold[m["mention_id"]] for m in c["mentions"] if norm(m["surface"]) == norm(hs)})
        # the entities sharing this surface must have disjoint co-mention signatures
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                assert not (decode_set(sig[ents[i]]) & decode_set(sig[ents[j]])), \
                    f"homograph entities {ents[i]},{ents[j]} share co-mentions"
