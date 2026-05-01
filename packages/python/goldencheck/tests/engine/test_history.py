import json

from goldencheck.engine import history as history_mod
from goldencheck.engine.history import record_scan, load_history, get_previous_scan
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import DatasetProfile, ColumnProfile


def _make_profile(rows: int = 10, cols: int = 2) -> DatasetProfile:
    columns = [
        ColumnProfile(
            name=f"col{i}",
            inferred_type="string",
            null_count=0,
            null_pct=0.0,
            unique_count=rows,
            unique_pct=100.0,
            row_count=rows,
        )
        for i in range(cols)
    ]
    return DatasetProfile(
        file_path="test.csv",
        row_count=rows,
        column_count=cols,
        columns=columns,
    )


def _make_findings() -> list[Finding]:
    return [
        Finding(severity=Severity.ERROR, column="col0", check="chk", message="err"),
        Finding(severity=Severity.WARNING, column="col1", check="chk", message="warn"),
    ]


def test_record_scan_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history_mod, "HISTORY_FILE", tmp_path / "history.jsonl")

    profile = _make_profile()
    record_scan("test.csv", profile, _make_findings())

    history_file = tmp_path / "history.jsonl"
    assert history_file.exists()
    lines = history_file.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["rows"] == 10
    assert data["columns"] == 2
    assert data["errors"] == 1
    assert data["warnings"] == 1
    assert data["findings_count"] == 2


def test_record_scan_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(history_mod, "HISTORY_FILE", tmp_path / "history.jsonl")

    profile = _make_profile()
    record_scan("test.csv", profile, [])
    record_scan("test.csv", profile, [])

    lines = (tmp_path / "history.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2


def test_load_history_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "HISTORY_FILE", tmp_path / "nonexistent.jsonl")
    assert load_history() == []


def test_load_history_returns_records(tmp_path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    record = {
        "timestamp": "2024-01-01T00:00:00",
        "file": "/abs/test.csv",
        "rows": 5,
        "columns": 2,
        "grade": "A",
        "score": 90,
        "errors": 0,
        "warnings": 1,
        "findings_count": 1,
    }
    history_file.write_text(json.dumps(record) + "\n")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    records = load_history()
    assert len(records) == 1
    assert records[0].grade == "A"
    assert records[0].rows == 5


def test_load_history_file_filter(tmp_path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    r1 = {"timestamp": "t1", "file": "/a.csv", "rows": 1, "columns": 1, "grade": "A", "score": 100, "errors": 0, "warnings": 0, "findings_count": 0}
    r2 = {"timestamp": "t2", "file": "/b.csv", "rows": 2, "columns": 1, "grade": "B", "score": 80, "errors": 0, "warnings": 0, "findings_count": 0}
    history_file.write_text(json.dumps(r1) + "\n" + json.dumps(r2) + "\n")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    records = load_history(file_filter="/a.csv")
    assert len(records) == 1
    assert records[0].file == "/a.csv"


def test_load_history_last_n(tmp_path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    lines = []
    for i in range(5):
        r = {"timestamp": f"t{i}", "file": "/f.csv", "rows": i, "columns": 1, "grade": "A", "score": 100, "errors": 0, "warnings": 0, "findings_count": 0}
        lines.append(json.dumps(r))
    history_file.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    records = load_history(last_n=2)
    assert len(records) == 2
    assert records[0].rows == 3
    assert records[1].rows == 4


def test_load_history_skips_bad_json(tmp_path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    good = {"timestamp": "t", "file": "/f.csv", "rows": 1, "columns": 1, "grade": "A", "score": 100, "errors": 0, "warnings": 0, "findings_count": 0}
    history_file.write_text("bad json\n" + json.dumps(good) + "\n")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    records = load_history()
    assert len(records) == 1


def test_get_previous_scan_returns_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "HISTORY_FILE", tmp_path / "nonexistent.jsonl")
    assert get_previous_scan("test.csv") is None


def test_get_previous_scan_returns_latest(tmp_path, monkeypatch):
    from pathlib import Path
    history_file = tmp_path / "history.jsonl"
    resolved = str(Path("test.csv").resolve())
    r1 = {"timestamp": "t1", "file": resolved, "rows": 1, "columns": 1, "grade": "B", "score": 80, "errors": 0, "warnings": 0, "findings_count": 0}
    r2 = {"timestamp": "t2", "file": resolved, "rows": 2, "columns": 1, "grade": "A", "score": 90, "errors": 0, "warnings": 0, "findings_count": 0}
    history_file.write_text(json.dumps(r1) + "\n" + json.dumps(r2) + "\n")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    result = get_previous_scan("test.csv")
    assert result is not None
    assert result.grade == "A"
    assert result.rows == 2
