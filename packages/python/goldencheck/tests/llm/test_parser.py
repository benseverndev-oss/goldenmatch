import json
from goldencheck.llm.parser import parse_llm_response
from goldencheck.llm.prompts import LLMResponse

def test_parse_valid_json():
    raw = json.dumps({
        "columns": {"email": {"semantic_type": "email", "issues": [], "upgrades": [], "downgrades": []}},
        "relations": [],
    })
    result = parse_llm_response(raw)
    assert isinstance(result, LLMResponse)
    assert "email" in result.columns

def test_parse_malformed_json_returns_none():
    result = parse_llm_response("this is not json")
    assert result is None

def test_parse_invalid_schema_returns_none():
    raw = json.dumps({"columns": "not a dict"})
    result = parse_llm_response(raw)
    assert result is None

def test_parse_with_markdown_fences():
    raw = '```json\n{"columns": {}, "relations": []}\n```'
    result = parse_llm_response(raw)
    assert isinstance(result, LLMResponse)
