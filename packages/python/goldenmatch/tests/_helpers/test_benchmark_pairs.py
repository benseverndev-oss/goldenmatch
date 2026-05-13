import polars as pl
from goldenmatch import DedupeResult, MatchResult

from tests._helpers.benchmark_pairs import (
    pairs_from_dedupe_result,
    pairs_from_match_result,
)


def test_dedupe_pairs_transitive_closure():
    result = DedupeResult(
        golden=None, dupes=None, unique=None,
        clusters={
            42: {"members": [3, 1, 2], "size": 3, "pair_scores": {}},
            43: {"members": [10, 11], "size": 2, "pair_scores": {}},
            44: {"members": [99], "size": 1, "pair_scores": {}},  # singleton
        },
        scored_pairs=[], stats={},
    )
    pairs = pairs_from_dedupe_result(result, id_column="ignored", source_df=None)
    assert pairs == {(1, 2), (1, 3), (2, 3), (10, 11)}


def test_match_pairs_direct():
    matched = pl.DataFrame({
        "__target_row_id__": [0, 1],
        "__ref_row_id__": [2, 3],
        "target_id": ["t1", "t2"],
        "ref_id": ["r1", "r2"],
    })
    result = MatchResult(matched=matched, unmatched=None, stats={}, postflight_report=None)
    pairs = pairs_from_match_result(result, target_id_col="target_id", ref_id_col="ref_id")
    assert pairs == {("t1", "r1"), ("t2", "r2")}
