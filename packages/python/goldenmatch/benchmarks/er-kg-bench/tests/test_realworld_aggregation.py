"""Real-world (Wikidata) aggregation capability bench -- unit tests over the TINY
fixture (wheel-free for the loader/generator/CLI; wheel-gated for the runner)."""
from __future__ import annotations

from erkgbench.qa_e2e.realworld import (
    _FIXTURE_DIR,
    generate_realworld_aggregation,
    load_realworld_entities,
)


def test_load_realworld_entities_maps_qid_canonical_aliases():
    ents = load_realworld_entities(_FIXTURE_DIR / "wikidata_companies_TINY.json")
    by_id = {e.id: e for e in ents}
    assert set(by_id) == {"Q1", "Q2", "Q3", "Q4"}
    assert by_id["Q1"].canonical == "Acme Holdings"
    assert "Acme" in by_id["Q1"].variants          # aliases -> variants
    assert by_id["Q1"].canonical not in by_id["Q1"].variants  # canonical excluded


def test_generate_realworld_aggregation_shapes_and_gold():
    docs, qs = generate_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json", ambiguity=1.0, seed=7)
    # one doc per (anchor, member) edge = 3
    assert len(docs) == 3
    # each doc text mentions the relation words and ends with a period
    assert all("has subsidiary" in d.text and d.text.endswith(".") for d in docs)
    # list + count question for the single anchor
    lists = [q for q in qs if q.kind == "list"]
    assert len(lists) == 1
    q = lists[0]
    assert q.anchor_id == "Q1" and q.relation == "has_subsidiary"
    assert set(q.gold_members) == {"Q2", "Q3", "Q4"} and q.gold_count == 3
    # uniqueness invariant: no duplicate (anchor_id, relation) across list questions
    keys = [(q.anchor_id, q.relation) for q in lists]
    assert len(keys) == len(set(keys))
    # ambiguity=1.0 -> at least one mention uses a non-canonical alias somewhere
    all_text = " ".join(d.text for d in docs)
    assert "Acme" in all_text or "BETA" in all_text or "Beta Corporation" in all_text
