"""Tests for FileProvider."""
from __future__ import annotations

from pathlib import Path

import pytest

from infermap.errors import InferMapError
from infermap.providers.file import FileProvider

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_csv_extraction_field_names():
    provider = FileProvider()
    schema = provider.extract(FIXTURES / "crm_export.csv")
    names = [f.name for f in schema.fields]
    assert "fname" in names
    assert "lname" in names
    assert "email_addr" in names


def test_csv_extraction_has_samples():
    provider = FileProvider()
    schema = provider.extract(FIXTURES / "crm_export.csv")
    for field in schema.fields:
        assert isinstance(field.sample_values, list)
        assert len(field.sample_values) > 0


def test_csv_extraction_has_stats():
    provider = FileProvider()
    schema = provider.extract(FIXTURES / "crm_export.csv")
    for field in schema.fields:
        assert field.value_count > 0
        assert 0.0 <= field.null_rate <= 1.0
        assert 0.0 <= field.unique_rate <= 1.0


def test_csv_source_name():
    provider = FileProvider()
    schema = provider.extract(FIXTURES / "crm_export.csv")
    assert "crm_export" in schema.source_name


def test_missing_file_raises():
    provider = FileProvider()
    with pytest.raises(InferMapError):
        provider.extract("/does/not/exist.csv")


def test_sample_size_configurable():
    provider = FileProvider()
    schema = provider.extract(FIXTURES / "crm_export.csv", sample_size=2)
    for field in schema.fields:
        assert len(field.sample_values) <= 2


def test_dtype_normalized_integer():
    provider = FileProvider()
    schema = provider.extract(FIXTURES / "crm_export.csv")
    zipcode_field = next(f for f in schema.fields if f.name == "zipcode")
    assert zipcode_field.dtype == "integer"


def test_string_path_accepted():
    provider = FileProvider()
    schema = provider.extract(str(FIXTURES / "crm_export.csv"))
    assert len(schema.fields) > 0
