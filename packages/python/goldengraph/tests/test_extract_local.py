"""Local-extractor prototype: REBEL output decoding + triples->Extraction. Pure
(no model download), so the parsing contract is locked offline."""
from __future__ import annotations

from goldengraph.extract_local import parse_rebel_triplets, triplets_to_extraction


def test_parse_rebel_triplets_canonical():
    # REBEL's linearized format for two triples sharing the head "Goldengraph".
    out = ("<triplet> Goldengraph <subj> Acme <obj> developed by "
           "<triplet> Acme <subj> Rocket <obj> made")
    triples = parse_rebel_triplets(out)
    assert ("Goldengraph", "developed by", "Acme") in triples
    assert ("Acme", "made", "Rocket") in triples


def test_triplets_to_extraction_dedups_entities_and_indexes_rels():
    triples = [("Nabbes", "wrote", "Play X"), ("Nabbes", "born in", "London")]
    ext = triplets_to_extraction(triples)
    names = [m.name for m in ext.mentions]
    assert names == ["Nabbes", "Play X", "London"]  # Nabbes deduped to one mention
    assert all(m.typ == "entity" for m in ext.mentions)
    assert (ext.relationships[0].subj, ext.relationships[0].predicate,
            ext.relationships[0].obj) == (0, "wrote", 1)
    assert (ext.relationships[1].subj, ext.relationships[1].obj) == (0, 2)


def test_triplets_to_extraction_drops_self_loops_and_empties():
    triples = [("A", "rel", "A"), ("", "rel", "B"), ("C", "rel", "D")]
    ext = triplets_to_extraction(triples)
    # self-loop A->A dropped; empty head dropped; only C->D survives
    assert len(ext.relationships) == 1
    assert ext.mentions[ext.relationships[0].subj].name == "C"
