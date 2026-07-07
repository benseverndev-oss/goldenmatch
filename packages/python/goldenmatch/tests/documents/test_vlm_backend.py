import json

from goldenmatch.documents.types import Field, PageImage, TargetSchema
from goldenmatch.documents.vlm_backend import VLMExtractor

SCHEMA = TargetSchema([Field("full_name"), Field("email")])
PAGES = [PageImage(b"\x89PNG\r\n\x1a\n0", 10, 10, 0)]


def _content(rows):
    # what the model is told to return: {"records": [{"values":..., "confidence":...}]}
    return {"choices": [{"message": {"content": json.dumps({"records": rows})}}]}


def test_extracts_single_record():
    rows = [{"values": {"full_name": "Ada", "email": "ada@x.io"},
             "confidence": {"full_name": 0.95, "email": 0.9}}]
    calls = []
    fake = lambda payload: (calls.append(payload), _content(rows))[1]
    out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
    assert out.error is None and len(out.rows) == 1
    assert out.rows[0].values == {"full_name": "Ada", "email": "ada@x.io"}
    assert out.rows[0].confidence["full_name"] == 0.95
    # payload carried a data-URI image and the model id
    assert calls[0]["model"] == "gpt-4o"
    assert "image_url" in json.dumps(calls[0])


def test_extracts_multiple_records_from_a_table():
    rows = [{"values": {"full_name": "Bo", "email": "bo@x.io"}, "confidence": {}},
            {"values": {"full_name": "Cy", "email": "cy@x.io"}, "confidence": {}}]
    fake = lambda payload: _content(rows)
    out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
    assert len(out.rows) == 2
    assert out.rows[1].values["full_name"] == "Cy"
    assert out.rows[1].confidence["email"] == 0.0  # missing conf -> 0.0


def test_unknown_keys_dropped_missing_fields_nulled():
    rows = [{"values": {"full_name": "Ada", "junk": "x"}, "confidence": {}}]
    fake = lambda payload: _content(rows)
    out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
    assert out.rows[0].values == {"full_name": "Ada", "email": None}


def test_malformed_json_is_not_retried():
    calls = {"n": 0}
    def fake(payload):
        calls["n"] += 1
        return {"choices": [{"message": {"content": "not json at all"}}]}
    out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake,
                       max_attempts=2).extract(PAGES, SCHEMA)
    assert out.rows == [] and out.error is not None
    assert calls["n"] == 1  # deterministic (temperature=0) parse failure: no retry


def test_transport_error_is_retried_then_recorded():
    calls = {"n": 0}
    def fake(payload):
        calls["n"] += 1
        raise OSError("connection reset")
    out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake,
                       max_attempts=3).extract(PAGES, SCHEMA)
    assert calls["n"] == 3  # retried the full attempt budget
    assert out.rows == [] and out.error is not None


def test_truncated_response_reported_as_error():
    def fake(payload):
        return {"choices": [{"message": {"content": "{}"},
                              "finish_reason": "length"}]}
    out = VLMExtractor(api_key="k", model="gpt-4o", transport=fake).extract(PAGES, SCHEMA)
    assert out.rows == []
    assert out.error is not None and "truncated" in out.error
