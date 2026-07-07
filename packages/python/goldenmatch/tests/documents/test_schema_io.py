import json

import pytest
from goldenmatch.documents.schema_io import (
    load_schema,
    save_schema,
    schema_from_dict,
    schema_to_dict,
)
from goldenmatch.documents.types import Field, TargetSchema

SCHEMA = TargetSchema([Field("full_name"), Field("email", kind="email", hint="work email")])


def test_round_trip_dict():
    d = schema_to_dict(SCHEMA)
    assert d == {"fields": [
        {"name": "full_name", "kind": "text", "hint": None},
        {"name": "email", "kind": "email", "hint": "work email"}]}
    assert schema_from_dict(d) == SCHEMA


def test_round_trip_file(tmp_path):
    p = tmp_path / "schema.json"
    save_schema(SCHEMA, p)
    assert json.loads(p.read_text())["fields"][0]["name"] == "full_name"
    assert load_schema(p) == SCHEMA


def test_load_rejects_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"nope": 1}')
    with pytest.raises(ValueError, match="fields"):
        load_schema(p)


def test_load_rejects_non_dict_field():
    with pytest.raises(ValueError):
        schema_from_dict({"fields": ["full_name"]})
