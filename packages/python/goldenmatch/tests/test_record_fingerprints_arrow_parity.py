"""Phase 3 (Rust): ``record_fingerprints_batch_arrow`` matches the
dict-shaped kernel.

GH issue #625 (Arrow-native roadmap Phase 3).

The Arrow Rust kernel must produce bit-identical fingerprints to the
existing dict-shaped ``record_fingerprints_batch`` (and therefore to
the per-record ``record_fingerprint``) on identical input.

Skipped when ``goldenmatch._native`` isn't built or doesn't yet
expose ``record_fingerprints_batch_arrow`` -- the dict kernel
fallback covers correctness in that case.
"""
from __future__ import annotations

import polars as pl
import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "record_fingerprints_batch_arrow"):
    pytest.skip(
        "native module loaded but record_fingerprints_batch_arrow not exposed; "
        "Rust kernel needs to be rebuilt against the Phase 3 PR.",
        allow_module_level=True,
    )


from goldenmatch.core._hashing import (
    record_fingerprint,
    record_fingerprints_batch,
    record_fingerprints_batch_arrow,
)


class TestArrowVsDictKernel:
    def test_simple_string_records(self):
        records = [
            {"first_name": "Alice", "last_name": "Smith", "zip": "12345"},
            {"first_name": "Bob", "last_name": "Jones", "zip": "67890"},
            {"first_name": "Carol", "last_name": "Smith", "zip": "12345"},
        ]
        dict_hashes = record_fingerprints_batch(records)
        df = pl.DataFrame(records)
        arrow_hashes = record_fingerprints_batch_arrow(df)
        assert arrow_hashes == dict_hashes

    def test_per_record_single_call_matches(self):
        """The Arrow batch result must match the per-record
        ``record_fingerprint`` output one row at a time."""
        records = [
            {"a": "hello", "b": "world"},
            {"a": "foo", "b": "bar"},
        ]
        df = pl.DataFrame(records)
        arrow_hashes = record_fingerprints_batch_arrow(df)
        per_record = [record_fingerprint(r) for r in records]
        assert arrow_hashes == per_record


class TestMixedTypes:
    def test_int_and_string_columns(self):
        records = [
            {"name": "Alice", "age": 30, "active": True},
            {"name": "Bob",   "age": 25, "active": False},
        ]
        dict_hashes = record_fingerprints_batch(records)
        df = pl.DataFrame(records)
        arrow_hashes = record_fingerprints_batch_arrow(df)
        assert arrow_hashes == dict_hashes

    def test_float_columns(self):
        records = [
            {"name": "Alice", "score": 0.95},
            {"name": "Bob",   "score": 0.85},
        ]
        dict_hashes = record_fingerprints_batch(records)
        df = pl.DataFrame(records)
        arrow_hashes = record_fingerprints_batch_arrow(df)
        assert arrow_hashes == dict_hashes


class TestNulls:
    def test_null_values_match_dict_kernel(self):
        """Polars null cells should map to ``FpValue::Null`` in the
        Arrow kernel and produce the same fingerprint as the dict
        kernel's ``None`` handling."""
        records = [
            {"name": "Alice", "middle": "Marie"},
            {"name": "Bob",   "middle": None},
        ]
        dict_hashes = record_fingerprints_batch(records)
        df = pl.DataFrame(records)
        arrow_hashes = record_fingerprints_batch_arrow(df)
        assert arrow_hashes == dict_hashes


class TestUnderscorePrefix:
    def test_dunder_columns_dropped(self):
        """``__row_id__`` etc. must NOT influence the fingerprint --
        same contract as the dict kernel."""
        records_with = [
            {"name": "Alice", "__row_id__": 1, "__source__": "fixture"},
            {"name": "Bob",   "__row_id__": 2, "__source__": "fixture"},
        ]
        records_without = [
            {"name": "Alice"},
            {"name": "Bob"},
        ]
        df_with = pl.DataFrame(records_with)
        df_without = pl.DataFrame(records_without)
        with_hashes = record_fingerprints_batch_arrow(df_with)
        without_hashes = record_fingerprints_batch_arrow(df_without)
        assert with_hashes == without_hashes


class TestErrors:
    def test_non_finite_float_raises(self):
        """``float("inf")`` is not canonicalizable -- same contract as
        the dict kernel."""
        df = pl.DataFrame({"name": ["Alice"], "score": [float("inf")]})
        with pytest.raises(Exception, match="non-finite"):
            record_fingerprints_batch_arrow(df)


class TestEmpty:
    def test_empty_dataframe(self):
        df = pl.DataFrame({"name": pl.Series([], dtype=pl.Utf8)})
        assert record_fingerprints_batch_arrow(df) == []


class TestScale:
    def test_100_row_parity(self):
        """Larger workload to exercise the rayon par_iter path."""
        import random
        rng = random.Random(7)
        records = [
            {
                "first": rng.choice(["alice", "bob", "carol", "dave"]),
                "last":  rng.choice(["smith", "jones", "doe", "lee"]),
                "zip":   str(10000 + rng.randint(0, 89999)),
                "age":   rng.randint(18, 90),
            }
            for _ in range(100)
        ]
        dict_hashes = record_fingerprints_batch(records)
        df = pl.DataFrame(records)
        arrow_hashes = record_fingerprints_batch_arrow(df)
        assert arrow_hashes == dict_hashes
