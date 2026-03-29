"""Tests for SchemaFileProvider."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import yaml

from infermap.errors import ConfigError
from infermap.providers.schema_file import SchemaFileProvider


@pytest.fixture
def tmp_yaml(tmp_path):
    """Write a YAML schema file with aliases and required fields."""
    data = {
        "fields": [
            {
                "name": "customer_id",
                "dtype": "integer",
                "aliases": ["cust_id", "id"],
                "required": True,
            },
            {
                "name": "email",
                "dtype": "string",
                "aliases": ["email_address", "email_addr"],
                "required": True,
            },
            {
                "name": "phone",
                "dtype": "string",
            },
        ]
    }
    p = tmp_path / "schema.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


@pytest.fixture
def tmp_json(tmp_path):
    """Write a JSON schema file."""
    data = {
        "fields": [
            {"name": "first_name", "dtype": "string"},
            {"name": "age", "dtype": "integer", "required": True},
        ]
    }
    p = tmp_path / "schema.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_yaml_field_names(tmp_yaml):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_yaml)
    names = [f.name for f in schema.fields]
    assert "customer_id" in names
    assert "email" in names
    assert "phone" in names


def test_yaml_dtypes(tmp_yaml):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_yaml)
    cid = next(f for f in schema.fields if f.name == "customer_id")
    assert cid.dtype == "integer"


def test_yaml_aliases_in_metadata(tmp_yaml):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_yaml)
    email_field = next(f for f in schema.fields if f.name == "email")
    assert "aliases" in email_field.metadata
    assert "email_address" in email_field.metadata["aliases"]
    assert "email_addr" in email_field.metadata["aliases"]


def test_yaml_required_fields(tmp_yaml):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_yaml)
    assert "customer_id" in schema.required_fields
    assert "email" in schema.required_fields
    assert "phone" not in schema.required_fields


def test_missing_fields_key_raises_config_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.dump({"version": 1}), encoding="utf-8")
    provider = SchemaFileProvider()
    with pytest.raises(ConfigError):
        provider.extract(bad)


def test_json_format_works(tmp_json):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_json)
    names = [f.name for f in schema.fields]
    assert "first_name" in names
    assert "age" in names


def test_json_required_fields(tmp_json):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_json)
    assert "age" in schema.required_fields
    assert "first_name" not in schema.required_fields


def test_field_dtype_defaults_to_string(tmp_path):
    data = {"fields": [{"name": "notes"}]}
    p = tmp_path / "schema.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    provider = SchemaFileProvider()
    schema = provider.extract(p)
    notes = next(f for f in schema.fields if f.name == "notes")
    assert notes.dtype == "string"


def test_source_name_from_stem(tmp_yaml):
    provider = SchemaFileProvider()
    schema = provider.extract(tmp_yaml)
    assert schema.source_name == "schema"


def test_string_path_accepted(tmp_yaml):
    provider = SchemaFileProvider()
    schema = provider.extract(str(tmp_yaml))
    assert len(schema.fields) == 3
