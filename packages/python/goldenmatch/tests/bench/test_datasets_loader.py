import importlib.util
from pathlib import Path
import pytest

# test file is at packages/python/goldenmatch/tests/bench/test_*.py
# parents: [bench, tests, goldenmatch, python, packages, <repo-root>] -> [5] = repo root
REPO = Path(__file__).resolve().parents[5]
SPEC = REPO / "scripts" / "bench_er_headtohead" / "datasets.py"

def _load():
    spec = importlib.util.spec_from_file_location("bench_datasets", SPEC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

@pytest.mark.benchmark
def test_load_historical_50k_shape_or_skip():
    mod = _load()
    try:
        records, truth = mod.load_dataset("historical_50k")
    except mod.DatasetUnavailable as e:
        pytest.skip(f"historical_50k unavailable: {e}")
    # records: polars DF with a 'record_id' col; truth: {record_id, cluster_id}
    assert "record_id" in records.columns
    assert set(truth.columns) >= {"record_id", "cluster_id"}
    assert records.height > 1000
    rec_ids = set(records["record_id"].to_list())
    assert set(truth["record_id"].to_list()).issubset(rec_ids)
