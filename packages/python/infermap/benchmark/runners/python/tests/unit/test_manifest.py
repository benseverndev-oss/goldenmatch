"""Tests for the manifest loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from infermap_bench.manifest import (
    CaseRef,
    IncompatibleManifestError,
    InvalidManifestError,
    load_manifest,
)
from infermap_bench import MANIFEST_VERSION


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _valid_entry(**overrides) -> dict:
    base = {
        "id": "valentine/magellan/foo",
        "path": "cases/valentine/foo",
        "category": "valentine",
        "subcategory": "magellan",
        "source": {
            "name": "foo",
            "url": "https://example.com",
            "license": "MIT",
            "attribution": "test",
        },
        "tags": ["alias_dominant"],
        "expected_difficulty": "easy",
        "field_counts": {"source": 4, "target": 4},
    }
    base.update(overrides)
    return base


def test_loads_valid_manifest(tmp_path):
    path = _write_manifest(tmp_path, {
        "version": 1,
        "generated_at": "2026-04-08T00:00:00Z",
        "cases": [_valid_entry()],
    })
    refs = load_manifest(path)
    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, CaseRef)
    assert ref.id == "valentine/magellan/foo"
    assert ref.category == "valentine"
    assert ref.expected_difficulty == "easy"
    assert ref.tags == ["alias_dominant"]
    assert ref.field_counts == {"source": 4, "target": 4}


def test_rejects_newer_version(tmp_path):
    path = _write_manifest(tmp_path, {
        "version": MANIFEST_VERSION + 1,
        "generated_at": "2026-04-08T00:00:00Z",
        "cases": [],
    })
    with pytest.raises(IncompatibleManifestError) as exc:
        load_manifest(path)
    assert str(MANIFEST_VERSION + 1) in str(exc.value)


def test_rejects_missing_version_field(tmp_path):
    path = _write_manifest(tmp_path, {"cases": []})
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_boolean_version(tmp_path):
    # Python treats `bool` as an `int` subclass, so this must be
    # explicitly guarded or {"version": true} silently loads as v1.
    path = _write_manifest(tmp_path, {
        "version": True,  # type: ignore[dict-item]
        "generated_at": "x",
        "cases": [],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_field_counts_non_integer_value(tmp_path):
    bad = _valid_entry(field_counts={"source": "four", "target": 4})
    path = _write_manifest(tmp_path, {
        "version": 1,
        "generated_at": "x",
        "cases": [bad],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_missing_cases_field(tmp_path):
    path = _write_manifest(tmp_path, {"version": 1, "generated_at": "x"})
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_invalid_difficulty(tmp_path):
    path = _write_manifest(tmp_path, {
        "version": 1,
        "generated_at": "x",
        "cases": [_valid_entry(expected_difficulty="impossible")],
    })
    with pytest.raises(InvalidManifestError) as exc:
        load_manifest(path)
    msg = str(exc.value)
    assert "expected_difficulty" in msg or "impossible" in msg


def test_rejects_invalid_category(tmp_path):
    path = _write_manifest(tmp_path, {
        "version": 1,
        "generated_at": "x",
        "cases": [_valid_entry(category="bogus")],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_missing_source_field(tmp_path):
    bad = _valid_entry()
    del bad["source"]
    path = _write_manifest(tmp_path, {
        "version": 1, "generated_at": "x", "cases": [bad],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_source_missing_license(tmp_path):
    bad = _valid_entry()
    del bad["source"]["license"]
    path = _write_manifest(tmp_path, {
        "version": 1, "generated_at": "x", "cases": [bad],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_non_string_tag(tmp_path):
    bad = _valid_entry(tags=["ok", 42])
    path = _write_manifest(tmp_path, {
        "version": 1, "generated_at": "x", "cases": [bad],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_rejects_field_counts_missing_target(tmp_path):
    bad = _valid_entry(field_counts={"source": 4})
    path = _write_manifest(tmp_path, {
        "version": 1, "generated_at": "x", "cases": [bad],
    })
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "does_not_exist.json")


def test_invalid_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InvalidManifestError):
        load_manifest(path)


def test_case_ref_is_immutable(tmp_path):
    path = _write_manifest(tmp_path, {
        "version": 1,
        "generated_at": "x",
        "cases": [_valid_entry()],
    })
    ref = load_manifest(path)[0]
    # Frozen dataclass — mutation should fail
    with pytest.raises(Exception):
        ref.id = "mutated"  # type: ignore[misc]
