import json
from pathlib import Path

from goldenflow.engine.manifest import Manifest, TransformRecord


def test_create_manifest():
    m = Manifest(source="data.csv")
    assert m.source == "data.csv"
    assert m.records == []


def test_add_record():
    m = Manifest(source="data.csv")
    m.add_record(TransformRecord(
        column="name",
        transform="strip",
        affected_rows=10,
        total_rows=100,
        sample_before=["  John  ", "  Jane  "],
        sample_after=["John", "Jane"],
    ))
    assert len(m.records) == 1
    assert m.records[0].column == "name"


def test_add_error():
    m = Manifest(source="data.csv")
    m.add_error(column="phone", transform="phone_e164", row=5, error="Parse failed")
    assert len(m.errors) == 1


def test_save_manifest(tmp_path: Path):
    m = Manifest(source="data.csv")
    m.add_record(TransformRecord(
        column="email",
        transform="lowercase",
        affected_rows=50,
        total_rows=100,
        sample_before=["JOHN@TEST.COM"],
        sample_after=["john@test.com"],
    ))
    path = tmp_path / "manifest.json"
    m.save(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["source"] == "data.csv"
    assert len(data["records"]) == 1
