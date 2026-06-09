"""Pure-Python coverage for the bloom CLK batch helper + refactor.

Runs WITHOUT the native module (no importorskip): guards that the extracted
parse/prepare/hash helpers and the column-level ``bloom_clk_batch`` wrapper are
byte-identical to the legacy per-row scalar transform. The native-vs-Python
byte parity lives in tests/test_native_bloom_parity.py (skipped without the ext).
"""
from __future__ import annotations

import pytest
from goldenmatch.utils import transforms as T

VALUES = ["john smith", "jon smyth", "a", "", "o'brien", "москва"]
TRANSFORMS = [
    "bloom_filter",
    "bloom_filter:standard",
    "bloom_filter:high",
    "bloom_filter:paranoid",
    "bloom_filter:2:20:512",
    "bloom_filter:2:20:512:mykey",
]


@pytest.mark.parametrize("transform", TRANSFORMS)
def test_batch_matches_scalar(monkeypatch, transform):
    """bloom_clk_batch (Python path) == apply_transform per row."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    expected = [T.apply_transform(v, transform) for v in VALUES]
    got = T.bloom_clk_batch(list(VALUES), transform)
    assert got == expected


def test_none_passthrough(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    out = T.bloom_clk_batch(["abc", None, ""], "bloom_filter")
    assert out[1] is None
    assert out[0] and out[2]


def test_parse_params():
    assert T._parse_bloom_params("bloom_filter") == (2, 20, 1024, None, False)
    assert T._parse_bloom_params("bloom_filter:standard") == (2, 20, 512, None, False)
    assert T._parse_bloom_params("bloom_filter:high") == (2, 30, 1024, "default_field_key", False)
    assert T._parse_bloom_params("bloom_filter:paranoid") == (3, 40, 2048, "default_field_key", True)
    assert T._parse_bloom_params("bloom_filter:2:16:512") == (2, 16, 512, None, False)
    assert T._parse_bloom_params("bloom_filter:2:16:512:k") == (2, 16, 512, "k", False)


def test_prepare_input_padding():
    # sub-ngram strings get _-padded to ngram length
    assert T._prepare_bloom_input("a", 3, False) == "a__"
    assert T._prepare_bloom_input("  AB  ", 2, False) == "ab"
    # balanced salt only for paranoid + short strings
    salted = T._prepare_bloom_input("ab", 2, True)
    assert len(salted) == 2 + 8 and salted.startswith("ab")
