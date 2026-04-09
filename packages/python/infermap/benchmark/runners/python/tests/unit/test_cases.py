"""Tests for the case loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from infermap_bench.cases import (
    Case,
    ExpectedCoverageError,
    FieldCountMismatchError,
    IncompleteCaseError,
    load_case,
)
from infermap_bench.manifest import CaseRef, CaseSource


def _make_ref(rel_path: str, field_counts: dict[str, int]) -> CaseRef:
    return CaseRef(
        id="test/dummy",
        path=rel_path,
        category="valentine",
        subcategory="test",
        source=CaseSource(name="x", url="x", license="MIT", attribution="x"),
        tags=[],
        expected_difficulty="easy",
        field_counts=field_counts,
    )


def _write_case(
    root: Path,
    source_csv: str,
    target_csv: str,
    expected: dict,
    case_meta: dict | None = None,
) -> Path:
    case_dir = root / "cases/valentine/test"
    case_dir.mkdir(parents=True)
    (case_dir / "source.csv").write_text(source_csv, encoding="utf-8")
    (case_dir / "target.csv").write_text(target_csv, encoding="utf-8")
    (case_dir / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    meta = case_meta or {"id": "test/dummy", "category": "valentine",
                         "subcategory": "test", "source": {"name": "x", "url": "x",
                         "license": "MIT", "attribution": "x"}, "tags": [],
                         "expected_difficulty": "easy", "notes": ""}
    (case_dir / "case.json").write_text(json.dumps(meta), encoding="utf-8")
    return case_dir


def test_loads_valid_case(tmp_path):
    _write_case(
        tmp_path,
        "fname,email_addr\nA,a@b.co\nB,b@c.co\n",
        "first_name,email\n,\n",
        {
            "mappings": [
                {"source": "fname", "target": "first_name"},
                {"source": "email_addr", "target": "email"},
            ],
            "unmapped_source": [],
            "unmapped_target": [],
        },
    )
    ref = _make_ref("cases/valentine/test", {"source": 2, "target": 2})
    case = load_case(tmp_path, ref)
    assert isinstance(case, Case)
    assert case.id == "test/dummy"
    assert case.category == "valentine"
    assert case.expected_difficulty == "easy"
    assert len(case.source_schema.fields) == 2
    assert len(case.target_schema.fields) == 2
    assert case.expected.mappings == [
        {"source": "fname", "target": "first_name"},
        {"source": "email_addr", "target": "email"},
    ]
    assert case.expected.unmapped_source == []
    assert case.expected.unmapped_target == []


def test_missing_source_csv(tmp_path):
    d = tmp_path / "cases/valentine/test"
    d.mkdir(parents=True)
    (d / "target.csv").write_text("x\n1\n", encoding="utf-8")
    (d / "expected.json").write_text('{"mappings": [], "unmapped_source": [], "unmapped_target": ["x"]}', encoding="utf-8")
    (d / "case.json").write_text("{}", encoding="utf-8")

    ref = _make_ref("cases/valentine/test", {"source": 0, "target": 1})
    with pytest.raises(IncompleteCaseError) as exc:
        load_case(tmp_path, ref)
    assert "source.csv" in str(exc.value)


def test_missing_expected_json(tmp_path):
    d = tmp_path / "cases/valentine/test"
    d.mkdir(parents=True)
    (d / "source.csv").write_text("a\n1\n", encoding="utf-8")
    (d / "target.csv").write_text("a\n1\n", encoding="utf-8")
    (d / "case.json").write_text("{}", encoding="utf-8")

    ref = _make_ref("cases/valentine/test", {"source": 1, "target": 1})
    with pytest.raises(IncompleteCaseError) as exc:
        load_case(tmp_path, ref)
    assert "expected.json" in str(exc.value)


def test_expected_coverage_violation_source(tmp_path):
    _write_case(
        tmp_path,
        "a,b\n1,2\n",
        "x\n1\n",
        {"mappings": [{"source": "a", "target": "x"}],
         "unmapped_source": [],  # missing 'b'
         "unmapped_target": []},
    )
    ref = _make_ref("cases/valentine/test", {"source": 2, "target": 1})
    with pytest.raises(ExpectedCoverageError) as exc:
        load_case(tmp_path, ref)
    assert "b" in str(exc.value)


def test_expected_coverage_violation_target(tmp_path):
    _write_case(
        tmp_path,
        "a\n1\n",
        "x,y\n1,2\n",
        {"mappings": [{"source": "a", "target": "x"}],
         "unmapped_source": [],
         "unmapped_target": []},  # missing 'y'
    )
    ref = _make_ref("cases/valentine/test", {"source": 1, "target": 2})
    with pytest.raises(ExpectedCoverageError) as exc:
        load_case(tmp_path, ref)
    assert "y" in str(exc.value)


def test_expected_coverage_overlap_raises(tmp_path):
    """A source column cannot be both mapped AND in unmapped_source."""
    _write_case(
        tmp_path,
        "a,b\n1,2\n",
        "x,y\n1,2\n",
        {"mappings": [{"source": "a", "target": "x"}, {"source": "b", "target": "y"}],
         "unmapped_source": ["a"],  # conflicts with mappings
         "unmapped_target": []},
    )
    ref = _make_ref("cases/valentine/test", {"source": 2, "target": 2})
    with pytest.raises(ExpectedCoverageError):
        load_case(tmp_path, ref)


def test_field_count_mismatch_source(tmp_path):
    _write_case(
        tmp_path,
        "a,b,c\n1,2,3\n",
        "x\n1\n",
        {"mappings": [{"source": "a", "target": "x"}],
         "unmapped_source": ["b", "c"],
         "unmapped_target": []},
    )
    ref = _make_ref("cases/valentine/test", {"source": 2, "target": 1})  # wrong
    with pytest.raises(FieldCountMismatchError) as exc:
        load_case(tmp_path, ref)
    assert "source" in str(exc.value).lower()


def test_field_count_mismatch_target(tmp_path):
    _write_case(
        tmp_path,
        "a\n1\n",
        "x,y,z\n1,2,3\n",
        {"mappings": [{"source": "a", "target": "x"}],
         "unmapped_source": [],
         "unmapped_target": ["y", "z"]},
    )
    ref = _make_ref("cases/valentine/test", {"source": 1, "target": 2})  # wrong
    with pytest.raises(FieldCountMismatchError) as exc:
        load_case(tmp_path, ref)
    assert "target" in str(exc.value).lower()


def test_expected_missing_mappings_key(tmp_path):
    _write_case(
        tmp_path,
        "a\n1\n",
        "x\n1\n",
        {"unmapped_source": ["a"], "unmapped_target": ["x"]},  # no 'mappings' key
    )
    ref = _make_ref("cases/valentine/test", {"source": 1, "target": 1})
    with pytest.raises(ExpectedCoverageError):
        load_case(tmp_path, ref)


def test_case_is_frozen(tmp_path):
    _write_case(
        tmp_path,
        "a\n1\n",
        "a\n1\n",
        {"mappings": [{"source": "a", "target": "a"}],
         "unmapped_source": [], "unmapped_target": []},
    )
    ref = _make_ref("cases/valentine/test", {"source": 1, "target": 1})
    case = load_case(tmp_path, ref)
    with pytest.raises(Exception):
        case.id = "mutated"  # type: ignore[misc]
