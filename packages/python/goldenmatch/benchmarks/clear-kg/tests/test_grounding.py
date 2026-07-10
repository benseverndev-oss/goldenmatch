"""Track C engine behaviors on a hand-built 3-triple fixture (one per class),
so each mechanism's failure mode is pinned in isolation."""
from grounding import (
    ground_ontology_conformance,
    ground_relation_aware,
    ground_sentence_presence,
    ground_ungrounded,
)

# Acme Labs partnered with Northwind Bank -> claim "acquired" is a DISTRACTOR
# (same ORG,ORG signature). Cedar Clinic / Osaka never co-occur -> HALLUCINATED.
_DOCS = {
    "C0": "Acme Labs acquired Northwind Bank.",           # supports the acquired claim
    "C1": "Ridge Analytics partnered with Delta Foods.",  # distractor for an acquired claim
}
_CANDS = [
    {"triple_id": "sup", "subj": "O0", "subj_surface": "Acme Labs", "subj_type": "ORG",
     "rel": "acquired", "obj": "O1", "obj_surface": "Northwind Bank", "obj_type": "ORG",
     "gold_verdict": "supported", "gold_class": "supported", "gold_provenance": {"doc_id": "C0", "span": [0, 32]}},
    {"triple_id": "dis", "subj": "O6", "subj_surface": "Ridge Analytics", "subj_type": "ORG",
     "rel": "acquired", "obj": "O7", "obj_surface": "Delta Foods", "obj_type": "ORG",
     "gold_verdict": "unsupported", "gold_class": "distractor", "gold_provenance": None},
    {"triple_id": "hal", "subj": "O2", "subj_surface": "Cedar Clinic", "subj_type": "ORG",
     "rel": "headquartered_in", "obj": "L2", "obj_surface": "Osaka", "obj_type": "PLACE",
     "gold_verdict": "unsupported", "gold_class": "hallucinated", "gold_provenance": None},
]


def _by_id(decisions):
    return {d["triple_id"]: d for d in decisions}


def test_ungrounded_asserts_everything_grounds_nothing():
    d = _by_id(ground_ungrounded(_CANDS, _DOCS))
    assert all(v["verdict"] == "supported" for v in d.values())   # asserts all
    assert all(not v["grounded"] for v in d.values())             # cites no span
    assert all(v["confidence"] is None for v in d.values())


def test_sentence_presence_grounds_the_distractor():
    d = _by_id(ground_sentence_presence(_CANDS, _DOCS))
    assert d["sup"]["verdict"] == "supported"
    assert d["dis"]["verdict"] == "supported"   # THE bug: co-occurrence != support
    assert d["hal"]["verdict"] == "unsupported"  # no co-occurrence -> caught


def test_ontology_conformance_grounds_distractor_and_hallucination():
    d = _by_id(ground_ontology_conformance(_CANDS, _DOCS))
    # types conform for all three -> all "supported", blind to the text
    assert d["sup"]["verdict"] == "supported"
    assert d["dis"]["verdict"] == "supported"
    assert d["hal"]["verdict"] == "supported"   # never in the corpus, yet "supported"


def test_relation_aware_separates_all_three():
    d = _by_id(ground_relation_aware(_CANDS, _DOCS))
    assert d["sup"]["verdict"] == "supported" and d["sup"]["grounded"]
    assert d["dis"]["verdict"] == "unsupported"   # sibling trigger, not "acquired"
    assert d["hal"]["verdict"] == "unsupported"
    # calibrated confidence: high on the real support, ~0 on the two rejects
    assert d["sup"]["confidence"] >= 0.9
    assert d["dis"]["confidence"] < 0.5 and d["hal"]["confidence"] < 0.5
