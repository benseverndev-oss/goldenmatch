"""Slice D KG-vs-KG scorecard -- wheel-free unit tests (no goldengraph_native)."""
from __future__ import annotations

from erkgbench.qa_e2e import kg_scorecard as ks


def test_parse_entity_set_finds_all_known_surfaces():
    s2c = {"Apple": "a", "Cupertino": "c", "Widgets": "w"}
    answer = "Apple, Cupertino and also Widgets."
    assert ks.parse_entity_set(answer, s2c) == {"a", "c", "w"}


def test_parse_entity_set_ignores_unknown_and_dedups():
    s2c = {"Apple": "a"}
    assert ks.parse_entity_set("Apple Apple Bogus", s2c) == {"a"}


def test_parse_entity_set_empty_on_no_match():
    assert ks.parse_entity_set("nothing here", {"Apple": "a"}) == set()
