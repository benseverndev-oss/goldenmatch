"""Tests for the canonical record fingerprint (goldenmatch.core._hashing).

Three layers:
  1. Pinned golden vectors -- computed directly from the canonical byte spec
     (independent of the implementation), so they catch any drift in the
     reference's byte assembly. Run everywhere.
  2. Property tests on the reference -- determinism, key-order independence,
     type-tag distinction, __-drop, -0.0 == 0.0, NaN/unsupported raise. Run
     everywhere.
  3. Native parity -- the Rust kernel must equal the Python reference on the
     whole battery. Skipped when goldenmatch._native isn't built (runs in the
     native CI lane).

Spec: docs/design/2026-05-26-stable-record-hash-cabi-plan.md.
"""
from __future__ import annotations

import pytest
from goldenmatch.core import _hashing, _native_loader

# Golden vectors computed from the canonical bytes, NOT from _fingerprint_py:
#   {}          -> sha256(b"")
#   {"a":"x"}   -> sha256(b"a" 0x1f b"s" b"x" 0x1e)
#   {"a":1}     -> sha256(b"a" 0x1f b"i" b"1" 0x1e)
#   {"n":1.5}   -> sha256(b"n" 0x1f b"f" b"3ff8000000000000" 0x1e)
_PINNED = [
    ({}, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ({"a": "x"}, "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"),
    ({"a": 1}, "b42e38730ddd9a099426dffa93926c03258ee2cd93f75204daa6f989af628206"),
    ({"n": 1.5}, "241b8cd11b575fd2b21e90b490f57fac54930f9a12124f23e284caa200c403a9"),
]

# Broader battery for property + native-parity checks.
_BATTERY = [
    {},
    {"a": "1"},
    {"a": 1},
    {"a": True},
    {"a": False},
    {"a": None},
    {"a": 1.5},
    {"a": -0.0},
    {"b": 2, "a": 1},                                 # key order
    {"a": 1, "__row_id__": 999},                      # __-prefixed dropped
    {"name": "Jörg", "city": "München"},    # unicode
    {"x": "a\x1eb\x1fc"},                             # separator chars in value
    {"first": "Alex", "last": "Smith", "email": "a@x.com"},
    {"big": 123456789012345678901234567890},          # arbitrary-precision int
]


@pytest.mark.parametrize("record,expected", _PINNED)
def test_reference_matches_pinned(record, expected):
    assert _hashing._fingerprint_py(record) == expected


@pytest.mark.parametrize("record,expected", _PINNED)
def test_public_uses_reference_when_native_off(monkeypatch, record, expected):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    assert _hashing.record_fingerprint(record) == expected


def test_is_64_lowercase_hex():
    fp = _hashing._fingerprint_py({"a": "x", "b": 2})
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_deterministic():
    rec = {"first": "Alex", "last": "Smith", "n": 3, "score": 0.5}
    assert _hashing._fingerprint_py(rec) == _hashing._fingerprint_py(dict(rec))


def test_key_order_independent():
    assert _hashing._fingerprint_py({"a": 1, "b": 2}) == _hashing._fingerprint_py(
        {"b": 2, "a": 1}
    )


def test_type_tags_distinguish_values():
    # int 1, str "1", bool True, float 1.0 must all hash differently.
    fps = {
        _hashing._fingerprint_py({"a": 1}),
        _hashing._fingerprint_py({"a": "1"}),
        _hashing._fingerprint_py({"a": True}),
        _hashing._fingerprint_py({"a": 1.0}),
    }
    assert len(fps) == 4


def test_underscore_prefixed_fields_dropped():
    assert _hashing._fingerprint_py({"a": 1, "__row_id__": 9}) == _hashing._fingerprint_py(
        {"a": 1}
    )


def test_negative_zero_equals_zero():
    assert _hashing._fingerprint_py({"a": -0.0}) == _hashing._fingerprint_py({"a": 0.0})


def test_value_with_separator_cannot_forge_field_boundary():
    # A value containing the framing bytes must not collide with a 2-field record.
    assert _hashing._fingerprint_py({"x": "a\x1eb"}) != _hashing._fingerprint_py(
        {"x": "a", "b": ""}
    )


def test_nan_and_inf_raise():
    with pytest.raises(ValueError):
        _hashing._fingerprint_py({"a": float("nan")})
    with pytest.raises(ValueError):
        _hashing._fingerprint_py({"a": float("inf")})


def test_unsupported_type_raises():
    with pytest.raises(TypeError):
        _hashing._fingerprint_py({"a": [1, 2, 3]})


# ── Native parity (runs in the native CI lane; skipped when ext absent) ──
pytestmark_native = pytest.mark.skipif(
    not _native_loader.native_available(),
    reason="goldenmatch._native not built",
)


@pytestmark_native
@pytest.mark.parametrize("record", _BATTERY)
def test_native_matches_reference(record):
    native = _native_loader.native_module().record_fingerprint(record)
    assert native == _hashing._fingerprint_py(record)


@pytestmark_native
@pytest.mark.parametrize("record,expected", _PINNED)
def test_native_matches_pinned(record, expected):
    assert _native_loader.native_module().record_fingerprint(record) == expected


@pytestmark_native
def test_public_uses_native_when_on(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    rec = {"first": "Alex", "last": "Smith"}
    assert _hashing.record_fingerprint(rec) == _native_loader.native_module().record_fingerprint(
        rec
    )
