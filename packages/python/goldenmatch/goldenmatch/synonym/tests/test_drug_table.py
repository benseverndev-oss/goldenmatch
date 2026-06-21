"""The drug knowledge-base table (the honest production path) resolves arbitrary
brand<->generic the morphological model provably can't. Auto-loaded per-domain."""

from __future__ import annotations

from rapidfuzz.distance import JaroWinkler

from goldenmatch.synonym.scorer import SynonymScorer


def test_drug_scorer_resolves_arbitrary_brands_via_table():
    s = SynonymScorer(domain="drug")  # auto-loads data/drug_synonyms.json
    assert s.score_pair("Advil", "ibuprofen") == 1.0
    assert s.score_pair("Viagra", "sildenafil") == 1.0
    assert s.score_pair("Coumadin", "warfarin") == 1.0
    # distinct drugs are NOT equivalent (the table asserts known synonyms only)
    assert s.score_pair("Advil", "Tylenol") < 1.0


def test_generic_domain_has_no_table_falls_back_to_jw():
    s = SynonymScorer(domain="generic")  # no data/generic_synonyms.json -> empty
    assert s.score_pair("Advil", "ibuprofen") == float(JaroWinkler.similarity("Advil", "ibuprofen"))


def test_table_beats_model_model_handles_offtable_morphology():
    s = SynonymScorer(domain="drug")
    assert s.score_pair("Advil", "ibuprofen") == 1.0  # table fast-path
    # cefuroxime/cefuroxim: NOT in the table -> trained model (morphological) scores high
    assert s.score_pair("cefuroxime", "cefuroxim") > 0.9
