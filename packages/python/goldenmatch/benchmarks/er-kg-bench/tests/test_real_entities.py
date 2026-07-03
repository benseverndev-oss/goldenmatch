"""GOLDENGRAPH_BENCH_ENTITIES=real feeds the engineered generator real records.csv entities."""
from erkgbench.qa_e2e.engineered import (
    _load_real_entities,
    emit_gold_mentions,
    generate_engineered,
)


def test_load_real_entities_groups_by_entity_id_verbatim():
    ents = _load_real_entities()
    assert len(ents) == 48
    ids = {e.id for e in ents}
    # ids are verbatim across 3 sources -- a QID, an rxcui:, and a slug all survive (NOT Q-filtered)
    assert any(i.startswith("Q") for i in ids)
    assert any(i.startswith("rxcui:") for i in ids)
    assert any("-" in i for i in ids)          # event slug
    # Q37156 (IBM) carries real aliases; canonical is not also a variant
    ibm = next(e for e in ents if e.id == "Q37156")
    assert ibm.canonical
    assert len(ibm.variants) >= 2
    assert ibm.canonical not in ibm.variants


def test_real_gate_yields_real_id_gold(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_BENCH_ENTITIES", "real")
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    gold_ids = {eid for eid, _s, _d in emit_gold_mentions(corpus.documents)}
    assert gold_ids
    assert all(i.startswith("Q") or i.startswith("rxcui:") or "-" in i for i in gold_ids)


def test_real_gate_off_by_default_uses_concepts(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_BENCH_ENTITIES", raising=False)
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    gold_ids = {eid for eid, _s, _d in emit_gold_mentions(corpus.documents)}
    # concept ids are gm:* / Q* (concepts_loader enforces ^(Q\d+|gm:...)$), never rxcui: -> real source unused
    assert not any(i.startswith("rxcui:") for i in gold_ids)
