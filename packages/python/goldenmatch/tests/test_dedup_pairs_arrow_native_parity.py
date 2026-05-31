"""Phase 3 (Rust): ``dedup_pairs_max_score_arrow`` matches both the
list-path and the columnar-path kernels.

GH issue #625 (Arrow-native roadmap Phase 3).

The Rust Arrow kernel must produce bit-identical output to the
existing dict-shaped kernel (``dedup_pairs_max_score``) and the
Polars columnar kernel (``dedup_pairs_max_score_columnar``) on
identical input.

Skipped when ``goldenmatch._native`` isn't built or doesn't yet
expose ``dedup_pairs_arrow`` -- the Polars fallback covers the
correctness contract in that case.
"""
from __future__ import annotations

import polars as pl
import pytest

native = pytest.importorskip("goldenmatch._native")
if not hasattr(native, "dedup_pairs_arrow"):
    pytest.skip(
        "native module loaded but dedup_pairs_arrow not exposed; "
        "Rust kernel needs to be rebuilt against the Phase 3 PR.",
        allow_module_level=True,
    )


from goldenmatch.core.pairs import (
    Pair,
    dedup_pairs_max_score,
    dedup_pairs_max_score_arrow,
    dedup_pairs_max_score_columnar,
)
from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA, pairs_list_to_df


def _df(pairs: list[Pair]) -> pl.DataFrame:
    return pairs_list_to_df(pairs)


def _to_list(df: pl.DataFrame) -> list[Pair]:
    return [
        (int(a), int(b), float(s))
        for a, b, s in zip(
            df["id_a"].to_list(),
            df["id_b"].to_list(),
            df["score"].to_list(),
            strict=True,
        )
    ]


class TestRustArrowParity:
    def test_simple_dedup_matches_legacy(self):
        pairs = [(1, 2, 0.9), (2, 1, 0.8), (3, 4, 0.95)]
        legacy = dedup_pairs_max_score(pairs)
        rust_arrow = _to_list(dedup_pairs_max_score_arrow(_df(pairs)))
        assert rust_arrow == legacy

    def test_multiple_duplicates_max_wins(self):
        pairs = [
            (1, 2, 0.5),
            (1, 2, 0.9),
            (1, 2, 0.7),
            (2, 1, 0.8),
        ]
        result = _to_list(dedup_pairs_max_score_arrow(_df(pairs)))
        assert result == [(1, 2, 0.9)]

    def test_sort_ascending_by_a_then_b(self):
        pairs = [
            (5, 6, 0.5),
            (1, 9, 0.6),
            (1, 2, 0.7),
            (3, 4, 0.8),
        ]
        result = _to_list(dedup_pairs_max_score_arrow(_df(pairs)))
        ids = [(a, b) for a, b, _ in result]
        assert ids == sorted(ids)

    def test_matches_polars_columnar_path(self):
        """Three-way parity: Rust Arrow == Polars columnar == legacy."""
        pairs = [
            (10, 20, 0.95), (20, 10, 0.96),
            (10, 30, 0.80),
            (30, 10, 0.85),
            (5,  5,  1.00),
            (1, 100, 0.50), (100, 1, 0.50),
            (1,  2, 0.99),
        ]
        legacy = dedup_pairs_max_score(pairs)
        polars_path = _to_list(dedup_pairs_max_score_columnar(_df(pairs)))
        rust_path = _to_list(dedup_pairs_max_score_arrow(_df(pairs)))
        assert rust_path == legacy
        assert rust_path == polars_path

    def test_empty_input(self):
        empty = pl.DataFrame(schema=PAIR_STREAM_SCHEMA)
        result = dedup_pairs_max_score_arrow(empty)
        assert result.is_empty()
        assert result.schema == PAIR_STREAM_SCHEMA

    def test_self_loops_preserved(self):
        """Pair (5, 5) is canonically (5, 5); the Arrow kernel must
        handle id_a == id_b without dropping or duplicating."""
        pairs = [(5, 5, 0.8), (5, 5, 0.9), (5, 5, 0.7)]
        result = _to_list(dedup_pairs_max_score_arrow(_df(pairs)))
        assert result == [(5, 5, 0.9)]

    def test_large_workload_matches(self):
        """100-pair workload exercising more of the BTreeMap reduce
        path. Ensures the Arrow buffer reads + reduction stay correct
        at non-trivial sizes."""
        import random
        rng = random.Random(42)
        pairs = []
        for _ in range(100):
            a = rng.randint(1, 30)
            b = rng.randint(1, 30)
            s = rng.random()
            pairs.append((a, b, s))
        legacy = dedup_pairs_max_score(pairs)
        rust_arrow = _to_list(dedup_pairs_max_score_arrow(_df(pairs)))
        assert rust_arrow == legacy
