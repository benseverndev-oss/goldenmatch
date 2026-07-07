import json

import pytest
from goldenmatch.documents.suggest import suggest_schema
from goldenmatch.documents.types import PageImage

PAGES = [PageImage(b"\x89PNG\r\n\x1a\n0", 10, 10, 0)]


def _resp(fields):
    return {"choices": [{"message": {"content": json.dumps({"fields": fields})}}]}


def test_suggests_fields_from_a_sample():
    fields = [{"name": "full_name", "kind": "text", "hint": "the person's name"},
              {"name": "email", "kind": "email"}]
    out = suggest_schema(PAGES, transport=lambda p: _resp(fields), model="gpt-4o")
    assert out.column_names() == ["full_name", "email"]
    assert out.fields[1].kind == "email"


def test_empty_fields_is_an_error():
    with pytest.raises(ValueError):
        suggest_schema(PAGES, transport=lambda p: _resp([]), model="gpt-4o")


def test_malformed_json_is_an_error():
    bad = {"choices": [{"message": {"content": "not json"}}]}
    with pytest.raises(ValueError):
        suggest_schema(PAGES, transport=lambda p: bad, model="gpt-4o")


def test_truncation_is_an_error():
    trunc = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
    with pytest.raises(ValueError, match="truncat"):
        suggest_schema(PAGES, transport=lambda p: trunc, model="gpt-4o")
