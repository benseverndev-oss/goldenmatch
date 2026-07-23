"""Real-world (Wikidata) aggregation capability bench -- unit tests over the TINY
fixture (wheel-free for the loader/generator/CLI; wheel-gated for the runner)."""
from __future__ import annotations

from erkgbench.qa_e2e.realworld import _FIXTURE_DIR, load_realworld_entities


def test_load_realworld_entities_maps_qid_canonical_aliases():
    ents = load_realworld_entities(_FIXTURE_DIR / "wikidata_companies_TINY.json")
    by_id = {e.id: e for e in ents}
    assert set(by_id) == {"Q1", "Q2", "Q3", "Q4"}
    assert by_id["Q1"].canonical == "Acme Holdings"
    assert "Acme" in by_id["Q1"].variants          # aliases -> variants
    assert by_id["Q1"].canonical not in by_id["Q1"].variants  # canonical excluded
