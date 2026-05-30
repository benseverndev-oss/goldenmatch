"""Parity: native record_fingerprints_batch vs the per-record loop.

Locks down the contract for the bulk kernel before the wire-up PR. The
bulk kernel must produce byte-identical output to calling
``record_fingerprint`` N times in sequence.

Skipped when the native module isn't built.
"""
from __future__ import annotations

import math

import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "record_fingerprints_batch"):
    pytest.skip(
        "native module loaded but record_fingerprints_batch not exposed",
        allow_module_level=True,
    )

from goldenmatch.core._hashing import record_fingerprint, record_fingerprints_batch


def _single_loop(records: list[dict]) -> list[str]:
    """Per-record single-call loop (the reference behavior the bulk kernel
    must reproduce byte-for-byte)."""
    return [record_fingerprint(r) for r in records]


class TestSingleRecord:
    def test_empty_batch(self):
        assert record_fingerprints_batch([]) == []

    def test_single_record(self):
        records = [{"name": "alice", "zip": "10001"}]
        assert record_fingerprints_batch(records) == _single_loop(records)


class TestSmallBatches:
    def test_two_distinct_records(self):
        records = [
            {"name": "alice", "zip": "10001"},
            {"name": "bob",   "zip": "10002"},
        ]
        assert record_fingerprints_batch(records) == _single_loop(records)

    def test_duplicate_records_same_hash(self):
        records = [
            {"name": "alice", "zip": "10001"},
            {"name": "alice", "zip": "10001"},
        ]
        out = record_fingerprints_batch(records)
        assert out[0] == out[1]
        assert out == _single_loop(records)

    def test_field_order_irrelevant(self):
        a = {"name": "alice", "zip": "10001"}
        b = {"zip": "10001", "name": "alice"}
        out = record_fingerprints_batch([a, b])
        assert out[0] == out[1]


class TestTypeCoverage:
    """All primitives the v1 canonical spec accepts."""

    def test_mixed_primitives(self):
        records = [
            {"s": "hello", "i": 42, "f": 3.14, "b": True, "n": None, "by": b"raw"},
            {"s": "world", "i": -1,  "f": 0.0,  "b": False,"n": None, "by": b""},
        ]
        assert record_fingerprints_batch(records) == _single_loop(records)

    def test_double_underscore_keys_dropped(self):
        # __row_id__, __source__ etc. must NOT contribute to the hash.
        a = {"name": "alice"}
        b = {"name": "alice", "__row_id__": 7, "__source__": "test"}
        out = record_fingerprints_batch([a, b])
        assert out[0] == out[1]


class TestErrorPropagation:
    def test_first_bad_record_raises(self):
        records = [
            {"name": "alice"},
            {"name": "bob", "bad": math.inf},  # non-finite float -> ValueError
            {"name": "carol"},
        ]
        with pytest.raises(ValueError, match="non-finite"):
            record_fingerprints_batch(records)

    def test_non_string_key_raises(self):
        records: list[dict] = [{"name": "alice"}, {1: "bob"}]
        with pytest.raises(TypeError, match="field names must be strings"):
            record_fingerprints_batch(records)


class TestScale:
    """Synthetic batch large enough to exercise the rayon parallel path."""

    def test_10k_records_parity(self):
        import random
        rng = random.Random(7)
        records = [
            {
                "first": rng.choice(["alice", "bob", "carol"]),
                "last":  rng.choice(["smith", "jones", "doe"]),
                "zip":   str(10000 + rng.randint(0, 89999)),
            }
            for _ in range(10_000)
        ]
        assert record_fingerprints_batch(records) == _single_loop(records)
