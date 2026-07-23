"""Real-world (Wikidata) aggregation capability bench -- unit tests over the TINY
fixture (wheel-free for the loader/generator/CLI; wheel-gated for the runner)."""
from __future__ import annotations

import pytest
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


def test_v1_fixture_loads_and_has_a_large_bucket_question():
    """Sanity-check the committed v1 fixture end-to-end (wheel-free): the loader +
    generator run over the real pull, and at least one anchor lands in the 11-20
    fan-out bucket (the bucket where the passage-window floor collapses)."""
    from erkgbench.qa_e2e.aggregation import size_bucket

    v1 = _FIXTURE_DIR / "wikidata_companies_v1.json"
    if not v1.exists():
        pytest.skip("wikidata_companies_v1.json fixture not committed")
    ents = load_realworld_entities(v1)
    assert len(ents) > 100
    _docs, qs = generate_realworld_aggregation(v1, ambiguity=0.6, seed=7)
    lists = [q for q in qs if q.kind == "list"]
    buckets = {size_bucket(q.gold_count) for q in lists}
    assert "11-20" in buckets       # the RAG-floor-collapse bucket is present
    # uniqueness invariant holds on the real pull too
    keys = [(q.anchor_id, q.relation) for q in lists]
    assert len(keys) == len(set(keys))


def test_run_realworld_aggregation_gg_beats_floor():
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")
    from erkgbench.qa_e2e.realworld import run_realworld_aggregation

    res = run_realworld_aggregation(
        _FIXTURE_DIR / "wikidata_companies_TINY.json",
        ambiguity=1.0, passage_k=2)
    # on the 3-member set, exact traversal should match all; the k=2 window can't
    gg = list(res.gg_setf1.values())
    assert gg and min(gg) >= 0.99            # exact traversal recovers the full set
