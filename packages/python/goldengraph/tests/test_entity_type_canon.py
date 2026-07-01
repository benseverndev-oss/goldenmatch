"""Deterministic coarse-type canonicalization (pure; no goldenmatch)."""
from goldengraph.schema import canonicalize_entity_type, entity_type_vocab


def test_exact_vocab_match_case_folded():
    assert canonicalize_entity_type("Organization") == "organization"
    assert canonicalize_entity_type("PERSON") == "person"


def test_substring_hint_maps_open_prose_to_coarse():
    # the real 7B jitter: all of these are one coarse class
    for t in ("Data Processing Technique", "Statistical Method", "Algorithm", "process", "metric"):
        assert canonicalize_entity_type(t) == "concept"
    assert canonicalize_entity_type("Tech Company") == "organization"


def test_off_vocab_falls_back_to_other():
    assert canonicalize_entity_type("wibble") == "other"
    assert canonicalize_entity_type("") == "other"


def test_custom_vocab_via_env(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_ENTITY_TYPE_VOCAB", "person, org")
    assert entity_type_vocab() == ("person", "org")
    # 'random' has no hint + no exact match; no 'other' in the custom vocab -> last-entry fallback
    assert canonicalize_entity_type("random", ("person", "org")) == "org"
