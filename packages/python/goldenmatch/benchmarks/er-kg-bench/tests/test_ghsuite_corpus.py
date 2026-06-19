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
