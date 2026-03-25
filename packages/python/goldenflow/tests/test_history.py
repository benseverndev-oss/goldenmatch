from pathlib import Path
from goldenflow.history import RunRecord, save_run, list_runs, generate_run_id

def test_generate_run_id():
    rid = generate_run_id()
    assert len(rid) > 10
    assert "_" in rid

def test_save_and_list_run(tmp_path, monkeypatch):
    monkeypatch.setattr("goldenflow.history.HISTORY_DIR", tmp_path)
    record = RunRecord(
        run_id="test_001",
        source="data.csv",
        timestamp="2024-01-01T00:00:00",
        rows=100,
        columns=5,
        transforms_applied=3,
        errors=0,
        duration_seconds=1.5,
    )
    save_run(record)

    runs = list_runs()
    assert len(runs) == 1
    assert runs[0].run_id == "test_001"
    assert runs[0].rows == 100
