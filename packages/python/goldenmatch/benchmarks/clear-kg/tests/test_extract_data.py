"""Track A dataset invariants: one doc per gold triple, homographs share a
surface, and homograph partners have DISJOINT object sets (so co-mention can
disambiguate them). Pure/offline."""
from collections import defaultdict

from er_utils import norm
from extract_data import generate_extraction_corpus


def test_one_doc_per_gold_triple():
    ds = generate_extraction_corpus(seed=0)
    assert len(ds["docs"]) == len(ds["gold"]) > 0


def test_homographs_share_a_surface_with_a_partner():
    ds = generate_extraction_corpus(seed=0)
    by_id = {e["entity_id"]: e for e in ds["entities"]}
    hg = ds["homograph_ids"]
    assert len(hg) >= 2
    # each homograph id shares at least one alias with another homograph id
    for a in hg:
        aliases_a = {norm(x) for x in by_id[a]["aliases"]}
        assert any(aliases_a & {norm(x) for x in by_id[b]["aliases"]}
                   for b in hg if b != a), a


def test_homograph_partners_have_disjoint_object_sets():
    ds = generate_extraction_corpus(seed=0)
    by_id = {e["entity_id"]: e for e in ds["entities"]}
    objs_of: dict[str, set] = defaultdict(set)
    for s, _r, o in ds["gold"]:
        objs_of[s].add(o)
    hg = ds["homograph_ids"]
    # any two homographs that SHARE a surface must not share an object
    for a in hg:
        for b in hg:
            if a >= b:
                continue
            shared_surface = ({norm(x) for x in by_id[a]["aliases"]}
                              & {norm(x) for x in by_id[b]["aliases"]})
            if shared_surface:
                assert not (objs_of[a] & objs_of[b]), (a, b)
