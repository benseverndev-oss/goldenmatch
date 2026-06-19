import json

import dataset.concepts_loader as cl  # pyright: ignore[reportMissingImports]
import erkgbench.run as run  # pyright: ignore[reportMissingImports]


def test_dataset_registry_paths():
    assert set(run.DATASETS) == {"wikidata", "ghsuite"}
    wd = run.DATASETS["wikidata"]
    assert wd["records"].name == "records.csv"
    assert wd["results_md"].name == "RESULTS.md"
    assert wd["results_json"].name == "results.json"
    gh = run.DATASETS["ghsuite"]
    assert gh["records"].name == "records_ghsuite.csv"
    assert gh["results_md"].name == "RESULTS_ghsuite.md"
    assert gh["results_json"].name == "results_ghsuite.json"


def test_load_records_takes_path():
    import inspect
    assert "records_path" in inspect.signature(run.load_records).parameters


_GOOD = {"concept": "Fellegi-Sunter", "canonical_id": "Q5442015", "entity_type": "concept",
         "context": "probabilistic record-linkage model",
         "variants": [{"surface": "F-S", "failure_class": "abbreviation"},
                      {"surface": "Fellegi-Sunter", "failure_class": "cross_document_exact"}]}


def test_load_concepts_parses(tmp_path):
    p = tmp_path / "c.jsonl"; p.write_text(json.dumps(_GOOD) + "\n", encoding="utf-8")
    out = cl.load_concepts(p)
    assert len(out) == 1 and out[0].canonical_id == "Q5442015"
    assert out[0].variants[0].failure_class == "abbreviation"
    assert out[0].variants[0].surface == "F-S"


def test_rejects_unknown_failure_class(tmp_path):
    import pytest
    bad = {**_GOOD, "variants": [{"surface": "x", "failure_class": "synonym"}]}  # not in the set
    p = tmp_path / "b.jsonl"; p.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="failure_class"):
        cl.load_concepts(p)


def test_rejects_bad_canonical_id(tmp_path):
    import pytest
    bad = {**_GOOD, "canonical_id": "12345"}  # not Q\d+ nor gm:*
    p = tmp_path / "b.jsonl"; p.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canonical_id"):
        cl.load_concepts(p)


def test_accepts_gm_namespaced_id(tmp_path):
    ok = {**_GOOD, "canonical_id": "gm:auto_config_controller"}
    p = tmp_path / "g.jsonl"; p.write_text(json.dumps(ok) + "\n", encoding="utf-8")
    assert cl.load_concepts(p)[0].canonical_id == "gm:auto_config_controller"


def test_valid_classes_match_run_class_order():
    # drift guard: the inlined set must equal the harness's CLASS_ORDER (this test
    # imports run -> goldenmatch, which is fine in the bench lane).
    import erkgbench.run as run  # pyright: ignore[reportMissingImports]
    assert cl.VALID_FAILURE_CLASSES == set(run.CLASS_ORDER)


import dataset.build_ghsuite as bg  # pyright: ignore[reportMissingImports]
import dataset.concepts_loader as cl  # noqa: F811


def test_assemble_drops_absent_keeps_found():
    concept = cl.Concept(
        concept="Fellegi-Sunter", canonical_id="Q5442015", entity_type="concept",
        context="probabilistic record-linkage model",
        variants=(cl.Variant("Fellegi-Sunter", "cross_document_exact"),
                  cl.Variant("F-S", "abbreviation"),
                  cl.Variant("NOTREAL", "synonym_brand")))
    # search_fn(surface) -> (found: bool, provenance: str | None)
    def search_fn(surface):
        hits = {"Fellegi-Sunter": "gh:goldenmatch:CLAUDE.md", "F-S": "gh:goldenmatch#1065"}
        return (surface in hits, hits.get(surface))
    rows = bg.assemble_records([concept], search_fn, start_id=0)
    assert {r["mention"] for r in rows} == {"Fellegi-Sunter", "F-S"}   # NOTREAL dropped
    assert all(r["entity_id"] == "Q5442015" for r in rows)
    assert all(r["entity_type"] == "concept" for r in rows)
    assert all(r["context"] == "probabilistic record-linkage model" for r in rows)  # canonical 1-liner, NOT a snippet
    fs = next(r for r in rows if r["mention"] == "F-S")
    assert fs["failure_class"] == "abbreviation"
    assert fs["source"].startswith("gh:")          # provenance captured
    assert [r["record_id"] for r in rows] == [0, 1] # contiguous ids
    assert set(rows[0].keys()) == {"record_id","mention","entity_type","context","entity_id","failure_class","source"}


def test_assemble_dedups_repeated_surface_within_concept():
    concept = cl.Concept(concept="LSH", canonical_id="Q6666443", entity_type="concept", context="x",
                         variants=(cl.Variant("LSH","cross_document_exact"), cl.Variant("LSH","abbreviation")))
    rows = bg.assemble_records([concept], lambda s: (True, "gh:x"), start_id=5)
    # one row per (concept, surface) found -> "LSH" appears once
    assert sum(1 for r in rows if r["mention"] == "LSH") == 1
    assert rows[0]["record_id"] == 5


def test_make_search_fn_empty_returns_callable_and_miss():
    # make_search_fn with no roots and no repos returns a callable that
    # returns (False, None) for any surface without raising.
    fn = bg.make_search_fn([], [])
    assert callable(fn)
    found, prov = fn("Fellegi-Sunter")
    assert found is False
    assert prov is None
    # Second call uses the cache path -- must not raise either.
    found2, prov2 = fn("Fellegi-Sunter")
    assert found2 is False
    assert prov2 is None
