"""Parity: native bloom_clk_batch vs the pure-Python CLK reference.

Locks down the contract for the CLK hash-loop kernel before it is added to
``_GATED_ON``. The native kernel must produce byte-identical hex to the
pure-Python ``_clk_from_prepared`` over the same prepared strings, and the
column-level ``bloom_clk_batch`` wrapper must behave identically under
``GOLDENMATCH_NATIVE=1`` (native) and ``=0`` (Python).

Skipped when the native module isn't built / doesn't expose the kernel.
"""
from __future__ import annotations

import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "bloom_clk_batch"):
    pytest.skip(
        "native module loaded but bloom_clk_batch not exposed -- rebuild",
        allow_module_level=True,
    )

from goldenmatch.utils import transforms as T

# Inputs spanning the parity-sensitive cases: short (sub-ngram), single char,
# empty, non-ASCII (code-point vs byte n-gram slicing), punctuation.
VALUES = [
    "john smith",
    "jon smyth",
    "a",
    "",
    "  Padded  ",
    "o'brien-mcdonald",
    "москва",  # москва (Cyrillic)
    "éèê name",  # accented latin
]

# Every spec shape: default, the three security presets, explicit params,
# explicit params + custom hmac key, and a trigram.
TRANSFORMS = [
    "bloom_filter",
    "bloom_filter:standard",
    "bloom_filter:high",
    "bloom_filter:paranoid",
    "bloom_filter:2:20:512",
    "bloom_filter:2:20:512:mykey",
    "bloom_filter:3:16:1024",
]


@pytest.mark.parametrize("transform", TRANSFORMS)
def test_kernel_matches_python_reference(transform):
    """Native kernel == pure-Python reference, byte-for-byte, per row."""
    ng, k, sz, key, bal = T._parse_bloom_params(transform)
    prepared = [T._prepare_bloom_input(v, ng, bal) for v in VALUES]
    py = [T._clk_from_prepared(p, ng, k, sz, key) for p in prepared]
    rs = native.bloom_clk_batch(prepared, ng, k, sz, key)
    assert rs == py


@pytest.mark.parametrize("transform", TRANSFORMS)
def test_batch_wrapper_native_vs_python(monkeypatch, transform):
    """bloom_clk_batch is output-identical with the gate on vs off."""
    values: list[str | None] = [*VALUES, None]

    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    py = T.bloom_clk_batch(values, transform)

    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    rs = T.bloom_clk_batch(values, transform)

    assert rs == py
    assert rs[-1] is None  # None passes through


def test_scalar_transform_unchanged():
    """The refactored scalar _bloom_filter_transform is still byte-identical to
    a single-row batch (guards the extraction refactor)."""
    for transform in TRANSFORMS:
        scalar = T._bloom_filter_transform("john smith", transform)
        batched = T.bloom_clk_batch(["john smith"], transform)[0]
        assert scalar == batched


def test_empty_and_none_rows():
    out = T.bloom_clk_batch(["abc", None, ""], "bloom_filter")
    assert out[1] is None
    assert len(out) == 3
    assert out[0] is not None and out[2] is not None


def test_invalid_filter_size_raises():
    with pytest.raises(Exception):
        native.bloom_clk_batch(["abc"], 2, 20, 7, None)  # 7 not a multiple of 8
