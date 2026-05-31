"""Phase 1a parity tests for columnar pair-stream functions.

GH issue #623 (Arrow-native roadmap Phase 1).

Asserts that the columnar entry points (``find_fuzzy_matches_columnar``,
``score_blocks_columnar``, ``build_clusters_columnar``) produce
byte-identical output to their list-based predecessors when the same
fixture, matchkey, and parameters are used. Phase 1a is purely a
shape change — same scoring, same canonicalization, same clustering.
Any drift here is a bug in the columnar wrappers, not a design
trade-off.

Tests use tiny hand-built fixtures (≤ 20 rows). NO ``dedupe_df``
calls, NO ``realistic_person_df`` calls — those are for the bench
harness, not unit tests.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.blocker import BlockResult
from goldenmatch.core.cluster import build_clusters, build_clusters_columnar
from goldenmatch.core.scorer import (
    PAIR_STREAM_SCHEMA,
    find_fuzzy_matches,
    find_fuzzy_matches_columnar,
    pairs_df_to_list,
    pairs_list_to_df,
    score_blocks_columnar,
    score_blocks_parallel,
)

# ── Fixtures ─────────────────────────────────────────────────────────


def _block_df(records: list[tuple[int, str]]) -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": [r[0] for r in records],
        "__source__": ["fixture"] * len(records),
        "name": [r[1] for r in records],
    })


def _block(records: list[tuple[int, str]], block_key: str = "k") -> BlockResult:
    return BlockResult(block_key=block_key, df=_block_df(records).lazy())


def _mk() -> MatchkeyConfig:
    return MatchkeyConfig(
        name="test",
        type="weighted",
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        threshold=0.85,
    )


# ── Adapter round-trip ──────────────────────────────────────────────


class TestAdapterRoundtrip:
    def test_empty_list_to_df_returns_empty_schema(self):
        df = pairs_list_to_df([])
        assert df.is_empty()
        assert df.schema == PAIR_STREAM_SCHEMA

    def test_empty_df_to_list_returns_empty(self):
        df = pl.DataFrame(schema=PAIR_STREAM_SCHEMA)
        assert pairs_df_to_list(df) == []

    def test_roundtrip_preserves_pairs(self):
        original = [(1, 2, 0.95), (3, 4, 0.87), (5, 6, 1.0)]
        df = pairs_list_to_df(original)
        back = pairs_df_to_list(df)
        assert back == original

    def test_df_schema_is_canonical(self):
        df = pairs_list_to_df([(1, 2, 0.9)])
        assert df.schema == PAIR_STREAM_SCHEMA
        assert df.height == 1


# ── find_fuzzy_matches parity ───────────────────────────────────────


class TestFindFuzzyMatchesParity:
    def test_single_block_parity(self):
        records = [
            (1, "John Smith"),
            (2, "Jon Smith"),
            (3, "Jane Smith"),
            (4, "John Smyth"),
            (5, "Bob Jones"),
        ]
        block_df = _block_df(records)
        mk = _mk()

        list_pairs = find_fuzzy_matches(block_df, mk)
        df_pairs = find_fuzzy_matches_columnar(block_df, mk)

        # Same pair set, same scores.
        assert pairs_df_to_list(df_pairs) == list_pairs

    def test_exclude_pairs_parity(self):
        records = [(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")]
        block_df = _block_df(records)
        mk = _mk()
        exclude = {(1, 2)}

        list_pairs = find_fuzzy_matches(block_df, mk, exclude_pairs=exclude)
        df_pairs = find_fuzzy_matches_columnar(block_df, mk, exclude_pairs=exclude)

        assert pairs_df_to_list(df_pairs) == list_pairs

    def test_empty_block_returns_empty(self):
        block_df = _block_df([(1, "alone")])
        mk = _mk()
        df_pairs = find_fuzzy_matches_columnar(block_df, mk)
        assert df_pairs.is_empty()
        assert df_pairs.schema == PAIR_STREAM_SCHEMA


# ── score_blocks parity ──────────────────────────────────────────────


class TestScoreBlocksParity:
    def test_single_block_parity(self):
        records = [
            (1, "John Smith"),
            (2, "Jon Smith"),
            (3, "Jane Smith"),
            (4, "John Smyth"),
        ]
        b = _block(records)
        mk = _mk()

        list_pairs = score_blocks_parallel([b], mk, matched_pairs=set())
        df_pairs = score_blocks_columnar([b], mk, matched_pairs=set())

        assert pairs_df_to_list(df_pairs) == list_pairs

    def test_multi_block_parity(self):
        b1 = _block([(10, "Alice Anderson"), (11, "Alice Andersen")], "ba")
        b2 = _block([(20, "Bob Brown"), (21, "Bob Browne")], "bb")
        mk = _mk()

        list_pairs = score_blocks_parallel([b1, b2], mk, matched_pairs=set())
        df_pairs = score_blocks_columnar([b1, b2], mk, matched_pairs=set())

        # Convert both to canonical sorted sets for order-independent compare
        # (score_blocks_parallel doesn't guarantee block iteration order
        # affects the output list order — DataFrame round-trip preserves it
        # exactly, but assert as sets to be safe).
        assert sorted(pairs_df_to_list(df_pairs)) == sorted(list_pairs)

    def test_empty_blocks_returns_empty(self):
        mk = _mk()
        df = score_blocks_columnar([], mk, matched_pairs=set())
        assert df.is_empty()
        assert df.schema == PAIR_STREAM_SCHEMA

    def test_matched_pairs_mutation_parity(self):
        """``score_blocks_parallel`` mutates ``matched_pairs`` in place
        as a side effect (existing contract). The columnar wrapper must
        preserve that exactly — callers across blocking passes rely on
        it."""
        records = [(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")]
        b = _block(records)
        mk = _mk()

        list_matched = set()
        score_blocks_parallel([b], mk, matched_pairs=list_matched)

        df_matched = set()
        score_blocks_columnar([b], mk, matched_pairs=df_matched)

        assert df_matched == list_matched, (
            "columnar wrapper failed to preserve matched_pairs side effect; "
            f"list_path={list_matched}, df_path={df_matched}"
        )


# ── build_clusters parity ────────────────────────────────────────────


class TestBuildClustersParity:
    def test_simple_clusters_parity(self):
        pairs = [(1, 2, 0.95), (2, 3, 0.92), (5, 6, 0.88)]
        all_ids = [1, 2, 3, 4, 5, 6, 7]

        legacy = build_clusters(pairs, all_ids=all_ids)
        columnar = build_clusters_columnar(
            pairs_list_to_df(pairs),
            all_ids=all_ids,
        )

        # Cluster dicts may differ in cluster_id ordering, but the
        # partition (mapping member_id -> set of members in same cluster)
        # must be identical.
        assert _partition(legacy) == _partition(columnar), (
            f"cluster partitions differ; legacy={_partition(legacy)}, "
            f"columnar={_partition(columnar)}"
        )

    def test_all_ids_derived_when_not_provided(self):
        pairs = [(1, 2, 0.9), (3, 4, 0.85)]
        # Both paths should infer the same all_ids from the pair stream.
        legacy = build_clusters(pairs)
        columnar = build_clusters_columnar(pairs_list_to_df(pairs))
        assert _partition(legacy) == _partition(columnar)

    def test_empty_pair_stream(self):
        legacy = build_clusters([], all_ids=[1, 2, 3])
        columnar = build_clusters_columnar(
            pl.DataFrame(schema=PAIR_STREAM_SCHEMA),
            all_ids=[1, 2, 3],
        )
        assert _partition(legacy) == _partition(columnar)


# ── Helpers ──────────────────────────────────────────────────────────


def _partition(clusters: dict[int, dict]) -> frozenset[frozenset[int]]:
    """Convert cluster dict to a partition (set of frozensets of members)
    that's invariant under cluster_id relabeling."""
    return frozenset(
        frozenset(c.get("members", []))
        for c in clusters.values()
    )
