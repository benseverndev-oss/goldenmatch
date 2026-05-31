"""Phase 3 parity: dedup_pairs_max_score_columnar matches the
list-path kernel.

GH issue #625 (Arrow-native roadmap Phase 3).

Asserts that the Polars-vectorized columnar dedup produces the same
output as the existing dict-shaped Python kernel
(``dedup_pairs_max_score``) on identical input. Tests cover:

- canonicalization (input pair orientation doesn't change output)
- max-score reduction across duplicate canonical pairs
- sort-order invariant ((id_a, id_b) ascending)
- empty / singleton edge cases
- tie behavior (output scores identical)

Both paths are bit-exact on the output VALUES; the list path's
strict ``>`` first-occurrence-wins tie behavior is invisible at the
output layer because both paths store the (same) max score.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.core.pairs import (
    Pair,
    dedup_pairs_max_score,
    dedup_pairs_max_score_columnar,
)
from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA, pairs_list_to_df


def _df_from_pairs(pairs: list[Pair]) -> pl.DataFrame:
    return pairs_list_to_df(pairs)


def _pairs_from_df(df: pl.DataFrame) -> list[Pair]:
    return [
        (int(a), int(b), float(s))
        for a, b, s in zip(
            df["id_a"].to_list(),
            df["id_b"].to_list(),
            df["score"].to_list(),
            strict=True,
        )
    ]


class TestEmpty:
    def test_empty_input_returns_empty_with_schema(self):
        empty = pl.DataFrame(schema=PAIR_STREAM_SCHEMA)
        result = dedup_pairs_max_score_columnar(empty)
        assert result.is_empty()
        assert result.schema == PAIR_STREAM_SCHEMA


class TestSimpleDedup:
    def test_no_duplicates_passes_through(self):
        pairs = [(1, 2, 0.9), (3, 4, 0.8), (5, 6, 0.7)]
        result = _pairs_from_df(dedup_pairs_max_score_columnar(_df_from_pairs(pairs)))
        # Should be sorted ascending by (id_a, id_b).
        assert result == sorted(pairs)

    def test_canonical_dedup_matches_list_path(self):
        pairs = [
            (1, 2, 0.9),
            (2, 1, 0.8),   # canonical dup of (1, 2) -- lower score
            (3, 4, 0.7),
            (4, 3, 0.9),   # canonical dup of (3, 4) -- HIGHER score
        ]
        legacy = dedup_pairs_max_score(pairs)
        columnar = _pairs_from_df(dedup_pairs_max_score_columnar(_df_from_pairs(pairs)))
        assert columnar == legacy

    def test_multiple_duplicates_max_wins(self):
        pairs = [
            (1, 2, 0.5),
            (1, 2, 0.9),  # winner
            (1, 2, 0.7),
            (2, 1, 0.8),  # canonical dup, doesn't beat 0.9
        ]
        result = _pairs_from_df(dedup_pairs_max_score_columnar(_df_from_pairs(pairs)))
        assert result == [(1, 2, 0.9)]


class TestSortOrder:
    def test_sort_ascending_by_id_a_then_id_b(self):
        pairs = [
            (5, 6, 0.5),
            (1, 9, 0.6),
            (1, 2, 0.7),
            (3, 4, 0.8),
        ]
        result = _pairs_from_df(dedup_pairs_max_score_columnar(_df_from_pairs(pairs)))
        ids = [(a, b) for a, b, _ in result]
        assert ids == sorted(ids)


class TestParityWithLegacy:
    def test_complex_workload_matches(self):
        """Larger workload with mix of dups, canonical reorientations,
        and unique pairs. Output must match the list path exactly."""
        pairs = [
            (10, 20, 0.95), (20, 10, 0.96),   # (10, 20) -> 0.96
            (10, 30, 0.80),                    # unique
            (30, 10, 0.85),                    # same canonical pair, 0.85 wins
            (5,  5,  1.00),                    # self-loop ok
            (1, 100, 0.50), (100, 1, 0.50),    # tie -> 0.50
            (1,  2, 0.99),
        ]
        legacy = dedup_pairs_max_score(pairs)
        columnar = _pairs_from_df(dedup_pairs_max_score_columnar(_df_from_pairs(pairs)))
        assert columnar == legacy

    def test_tie_behavior_output_values_identical(self):
        """On a score tie, both paths store the same max value -- the
        list path's strict-> first-occurrence-wins is invisible at the
        output value layer."""
        pairs = [(1, 2, 0.85), (1, 2, 0.85), (2, 1, 0.85)]
        legacy = dedup_pairs_max_score(pairs)
        columnar = _pairs_from_df(dedup_pairs_max_score_columnar(_df_from_pairs(pairs)))
        assert columnar == legacy
        assert columnar == [(1, 2, 0.85)]


class TestSchemaPreservation:
    def test_output_has_canonical_schema(self):
        pairs = [(1, 2, 0.9), (3, 4, 0.8)]
        result = dedup_pairs_max_score_columnar(_df_from_pairs(pairs))
        assert result.schema == PAIR_STREAM_SCHEMA
        assert result.height == 2
