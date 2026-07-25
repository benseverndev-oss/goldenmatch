"""Phase 1 (temporal as-of) real-world capability tests. Wheel-free tests cover the
generator + the temporal-blind floor (the RAG that can't answer 'as of a past date');
the wheel-gated test exercises the full store.as_of win."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e.realworld_temporal import (
    _FIXTURE_DIR,
    generate_realworld_temporal,
    load_temporal_entities,
)

_TINY = _FIXTURE_DIR / "wikidata_ceo_temporal_TINY.json"


def test_generate_temporal_shapes_and_gold():
    """Per succession fact: two dated passages + a PAST question (gold = earlier CEO) and a
    CURRENT question (gold = later CEO)."""
    docs, facts, qs = generate_realworld_temporal(_TINY, seed=7)
    assert len(docs) == 2                      # one 'As of ...' + one 'From ...'
    assert len(facts) == 1
    past = next(q for q in qs if q.regime == "past")
    cur = next(q for q in qs if q.regime == "current")
    assert past.gold_obj == "Q2"               # Alice, the earlier CEO
    assert cur.gold_obj == "Q3"                # Bob, after the 2019 succession
    assert past.D < 2019 <= cur.D              # the correction year is tc=2019
    # the two passages name the two CEOs
    text = " ".join(d.text for d in docs)
    assert "Alice Anderson" in text and "Bob Brown" in text


def test_temporal_blind_floor_is_wrong_on_past_right_on_current():
    """The capability gap, wheel-free: the temporal-blind floor returns the LAST-mentioned
    CEO regardless of the query date -> correct on the current regime, WRONG on the past
    regime (it hands back the corrected-away value)."""
    from erkgbench.qa_e2e.temporal import temporal_blind_floor

    docs, _facts, qs = generate_realworld_temporal(_TINY, seed=7)
    canon = load_temporal_entities(_TINY)
    s2c = {name: qid for qid, name in canon.items()}
    anchor_surfs = {"Acme Corp"}
    for q in qs:
        got = temporal_blind_floor(docs, anchor_surfs, q.relation, q.D, surface_to_canon=s2c)
        if q.regime == "current":
            assert got == q.gold_obj == "Q3"   # last CEO -> right on current
        else:
            assert got == "Q3" and q.gold_obj == "Q2"  # last CEO -> WRONG on past


def test_run_temporal_gg_beats_floor_on_past():
    """Wheel-gated: goldengraph store.as_of respects valid-time (right in BOTH regimes)
    while the temporal-blind floor collapses on the PAST regime."""
    try:
        import goldengraph_native  # noqa: F401
    except ImportError:
        pytest.skip("goldengraph-native wheel not installed")
    from erkgbench.qa_e2e.realworld_temporal import run_realworld_temporal

    res = run_realworld_temporal(_TINY)
    assert res.gg_acc.get("past", 0.0) >= 0.99      # KG right on past (respects valid-time)
    assert res.gg_acc.get("current", 0.0) >= 0.99   # and current
    assert res.floor_acc.get("current", 0.0) >= 0.99  # floor OK on current (not broken)
    # the capability: KG beats the temporal-blind floor on PAST queries
    assert res.gg_acc.get("past", 0.0) - res.floor_acc.get("past", 0.0) >= 0.5


def test_v1_fixture_loads_and_generates():
    """The committed real v1 fixture flows through the generator: real successions ->
    past+current questions with distinct gold, past date < the succession year."""
    v1 = _FIXTURE_DIR / "wikidata_ceo_temporal_v1.json"
    docs, facts, qs = generate_realworld_temporal(v1, seed=7)
    assert len(facts) >= 50 and len(docs) == 2 * len(facts)
    for q in qs:
        if q.regime == "past":
            assert q.gold_obj != next(
                x.gold_obj for x in qs if x.regime == "current" and x.anchor_id == q.anchor_id)
