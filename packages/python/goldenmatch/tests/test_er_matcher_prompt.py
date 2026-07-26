"""Tests for the ER-matcher prompt/serializer/parser (P2).

Locks the three properties the model contract depends on: deterministic
serialization (train == inference), a robust verdict parser that ABSTAINS (never
crashes) on bad output, and the missing != conflict rendering.
"""
from __future__ import annotations

from goldenmatch.core.er_matcher import (
    SERIALIZER_VERSION,
    build_chat,
    parse_verdict,
    render_target,
    serialize_pair_v1,
)


def test_serializer_is_deterministic_regardless_of_key_order():
    a = {"last_name": "Smith", "first_name": "John", "email": "j@x.com"}
    b = {"email": "j@x.com", "first_name": "Jon", "last_name": "Smith"}
    # Same data, different insertion order -> byte-identical rendering.
    s1 = serialize_pair_v1(a, b)
    s2 = serialize_pair_v1(dict(reversed(list(a.items()))), dict(reversed(list(b.items()))))
    assert s1 == s2
    # Sorted union of keys, both records fully rendered.
    assert "  email: j@x.com" in s1
    assert "Record A:" in s1 and "Record B:" in s1


def test_missing_field_is_explicit_not_blank():
    a = {"first_name": "John", "email": "j@x.com"}
    b = {"first_name": "John"}  # email missing on B
    s = serialize_pair_v1(a, b)
    # B's email is shown as the explicit sentinel (rubric: missing != conflict),
    # and A's email is present -> the union includes email for both.
    assert "email: (missing)" in s
    assert "email: j@x.com" in s


def test_none_and_empty_render_as_missing():
    s = serialize_pair_v1({"x": None, "y": "  ", "z": "v"}, {"x": "", "z": "v"})
    # union keys = {x,y,z}: A.x(None), A.y(blank), B.x(empty), B.y(absent) -> 4.
    assert s.count("(missing)") == 4


def test_build_chat_shape():
    chat = build_chat({"a": "1"}, {"a": "2"})
    assert [m["role"] for m in chat] == ["system", "user"]
    assert "Record A:" in chat[1]["content"]


def test_render_target_is_compact_json():
    out = render_target(True, 0.912345, "emails agree")
    assert out == '{"match":true,"confidence":0.912,"reason":"emails agree"}'


def test_parse_clean_json():
    v = parse_verdict('{"match": true, "confidence": 0.9, "reason": "ok"}')
    assert v == {"match": True, "confidence": 0.9, "reason": "ok"}


def test_parse_tolerates_prose_and_fences():
    v = parse_verdict('Sure!\n```json\n{"match": false, "confidence": 0.2}\n```\n')
    assert v is not None and v["match"] is False and v["confidence"] == 0.2


def test_parse_coerces_string_match_and_clamps_confidence():
    assert parse_verdict('{"match": "yes", "confidence": 5}')["match"] is True
    assert parse_verdict('{"match": "yes", "confidence": 5}')["confidence"] == 1.0
    assert parse_verdict('{"match": "no", "confidence": -3}')["confidence"] == 0.0


def test_parse_abstains_on_garbage():
    for bad in ["", "not json at all", "{oops", '{"confidence": 0.5}', '{"match": "maybe"}', "[]"]:
        assert parse_verdict(bad) is None, bad


def test_parse_defaults_confidence_when_absent():
    assert parse_verdict('{"match": true}')["confidence"] == 1.0
    assert parse_verdict('{"match": false}')["confidence"] == 0.0


def test_serializer_version_pinned():
    # A change here means the model must be retrained -> make it a conscious bump.
    assert SERIALIZER_VERSION == "v1"
