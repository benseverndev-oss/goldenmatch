"""The three canonicalization modes on a hand-built homograph fixture: exact
misses the alias, relaxed mis-credits the homograph, er_aware scores correctly."""
from extract_score import score_extraction

# P00 "Jane Smith" and P01 "John Doe" both carry the ambiguous alias "J. Smith";
# their objects are disjoint (Acme Labs vs Delta Foods), so co-mention resolves.
_DS = {
    "entities": [
        {"entity_id": "P00", "type": "PERSON", "canonical": "Jane Smith",
         "aliases": ["Jane Smith", "J. Smith", "Smith"]},
        {"entity_id": "P01", "type": "PERSON", "canonical": "John Doe",
         "aliases": ["John Doe", "J. Doe", "Doe", "J. Smith"]},
        {"entity_id": "O00", "type": "ORG", "canonical": "Acme Labs", "aliases": ["Acme Labs"]},
        {"entity_id": "O01", "type": "ORG", "canonical": "Delta Foods", "aliases": ["Delta Foods"]},
    ],
    "gold": [("P00", "employed_by", "O00"), ("P01", "employed_by", "O01")],
    "docs": {"D0": "J. Smith works at Acme Labs.", "D1": "J. Smith works at Delta Foods."},
    "homograph_ids": ["P00", "P01"],
}
# a faithful extractor's output: both subjects surface as the ambiguous "J. Smith"
_PREDS = [("J. Smith", "works at", "Acme Labs", "D0"),
          ("J. Smith", "works at", "Delta Foods", "D1")]


def test_exact_misses_the_alias():
    s = score_extraction(_PREDS, _DS, "exact")
    assert s["tp"] == 0  # "J. Smith" is nobody's canonical string


def test_relaxed_miscredits_the_homograph():
    s = score_extraction(_PREDS, _DS, "relaxed")
    # tie-breaks "J. Smith" to P00 for BOTH -> only the P00 triple is a true match
    assert s["tp"] == 1
    assert s["homograph_recall"] == 0.5


def test_er_aware_resolves_both_via_comention():
    s = score_extraction(_PREDS, _DS, "er_aware")
    assert s["tp"] == 2  # object disambiguates which "J. Smith"
    assert s["homograph_recall"] == 1.0
    assert s["f1"] == 1.0
