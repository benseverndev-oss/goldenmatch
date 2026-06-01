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
from goldenmatch.config.schemas import (
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
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


# ── Phase 1c hot-path direct-emit (#623) ────────────────────────────


class TestHotPathDirectEmit:
    """Phase 1c: ``find_fuzzy_matches(..., _emit_dataframe=True)`` must
    emit a ``pl.DataFrame`` directly from numpy arrays on the hot path
    (no NE, no exclude_pairs, no pre_scored_pairs), bypassing the
    list-of-tuples construction that dominates wall at 200M-pair scale.
    """

    def test_hot_path_returns_dataframe(self):
        block_df = _block_df([(1, "John Smith"), (2, "Jon Smith"), (3, "John Smyth")])
        result = find_fuzzy_matches(
            block_df, _mk(),
            exclude_pairs=None, pre_scored_pairs=None,
            _emit_dataframe=True,
        )
        assert isinstance(result, pl.DataFrame), (
            f"hot path with _emit_dataframe=True must return DataFrame, "
            f"got {type(result).__name__}"
        )
        from goldenmatch.core.scorer import PAIR_STREAM_SCHEMA
        assert result.schema == PAIR_STREAM_SCHEMA

    def test_hot_path_matches_list_path(self):
        """DataFrame-emit output must match list-emit output as
        canonical-pair sets."""
        block_df = _block_df([
            (1, "John Smith"), (2, "Jon Smith"), (3, "Jane Smith"),
            (4, "John Smyth"), (5, "Bob Jones"),
        ])
        list_pairs = find_fuzzy_matches(block_df, _mk())
        df_pairs = find_fuzzy_matches(block_df, _mk(), _emit_dataframe=True)
        assert isinstance(df_pairs, pl.DataFrame)

        list_set = {(min(a, b), max(a, b)) for a, b, _ in list_pairs}
        df_set = {
            (min(int(a), int(b)), max(int(a), int(b)))
            for a, b in zip(
                df_pairs["id_a"].to_list(),
                df_pairs["id_b"].to_list(),
                strict=True,
            )
        }
        assert df_set == list_set

    def test_exclude_pairs_path_emits_dataframe(self):
        """Task 1.1 (#623): ALL branches honor ``_emit_dataframe=True``.
        The ``exclude_pairs`` branch now emits a ``pl.DataFrame`` with
        ``PAIR_STREAM_SCHEMA`` instead of a list. Default
        (``_emit_dataframe=False``) keeps the list contract for the
        deprecation window."""
        block_df = _block_df([(1, "John"), (2, "Jon")])
        result = find_fuzzy_matches(
            block_df, _mk(), exclude_pairs={(1, 2)}, _emit_dataframe=True,
        )
        assert isinstance(result, pl.DataFrame)
        assert result.schema == PAIR_STREAM_SCHEMA
        # Default still returns a list (deprecation window).
        as_list = find_fuzzy_matches(block_df, _mk(), exclude_pairs={(1, 2)})
        assert isinstance(as_list, list)


# ── Task 1.1: uniform DataFrame from non-hot branches (#623) ─────────


class TestNonHotPathDirectEmit:
    """Task 1.1 (#623): ``find_fuzzy_matches(..., _emit_dataframe=True)``
    must emit a ``pl.DataFrame`` from ALL THREE branches (NE-penalty,
    ``exclude_pairs``, hot path), not just the hot path. Parity is
    asserted byte-for-byte against the list path via
    ``pairs_df_to_list``."""

    def test_exclude_pairs_branch_emits_dataframe(self):
        records = [
            (1, "John Smith"), (2, "Jon Smith"),
            (3, "Jane Smith"), (4, "John Smyth"),
        ]
        block_df = _block_df(records)
        mk = _mk()
        excl = {(1, 2)}

        as_list = find_fuzzy_matches(block_df, mk, exclude_pairs=excl)
        as_df = find_fuzzy_matches(
            block_df, mk, exclude_pairs=excl, _emit_dataframe=True,
        )
        assert isinstance(as_df, pl.DataFrame)
        assert as_df.columns == ["id_a", "id_b", "score"]
        assert pairs_df_to_list(as_df) == as_list

    def test_ne_branch_emits_dataframe(self):
        # name agrees (fuzzy weighted matchkey) but the NE field (city)
        # disagrees -> NE-penalty branch is exercised.
        block_df = pl.DataFrame({
            "__row_id__": [1, 2, 3],
            "__source__": ["fixture"] * 3,
            "name": ["John Smith", "Jon Smith", "John Smith"],
            "city": ["Boston", "Boston", "Seattle"],
        })
        mk = MatchkeyConfig(
            name="test_ne",
            type="weighted",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            threshold=0.85,
            negative_evidence=[
                NegativeEvidenceField(
                    field="city", scorer="exact", threshold=1.0, penalty=0.5,
                ),
            ],
        )

        as_list = find_fuzzy_matches(block_df, mk)
        as_df = find_fuzzy_matches(block_df, mk, _emit_dataframe=True)
        assert isinstance(as_df, pl.DataFrame)
        assert as_df.schema == PAIR_STREAM_SCHEMA
        assert pairs_df_to_list(as_df) == as_list

    def test_ne_branch_with_exclude_emits_dataframe(self):
        # NE-penalty AND exclude_pairs together (the nested exclude
        # filter inside the NE branch).
        block_df = pl.DataFrame({
            "__row_id__": [1, 2, 3],
            "__source__": ["fixture"] * 3,
            "name": ["John Smith", "Jon Smith", "John Smyth"],
            "city": ["Boston", "Boston", "Boston"],
        })
        mk = MatchkeyConfig(
            name="test_ne",
            type="weighted",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            threshold=0.85,
            negative_evidence=[
                NegativeEvidenceField(
                    field="city", scorer="exact", threshold=1.0, penalty=0.1,
                ),
            ],
        )
        excl = {(1, 2)}

        as_list = find_fuzzy_matches(block_df, mk, exclude_pairs=excl)
        as_df = find_fuzzy_matches(
            block_df, mk, exclude_pairs=excl, _emit_dataframe=True,
        )
        assert isinstance(as_df, pl.DataFrame)
        assert pairs_df_to_list(as_df) == as_list

    def test_empty_result_emits_empty_dataframe(self):
        # exclude_pairs removes the only candidate -> empty frame, not [].
        block_df = _block_df([(1, "John"), (2, "Jon")])
        as_df = find_fuzzy_matches(
            block_df, _mk(), exclude_pairs={(1, 2)}, _emit_dataframe=True,
        )
        assert isinstance(as_df, pl.DataFrame)
        assert as_df.schema == PAIR_STREAM_SCHEMA


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
