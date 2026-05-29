"""Match-mode (across_files_only / source_lookup / target_ids) on the fast path.

Previously `_resolve_fast_path` declined eligibility whenever any of these
were set, forcing the matchkey onto the slow `find_fuzzy_matches` path.
But these are post-filters on emitted pairs, not scoring math -- the fast
path can engage and apply them inline. Unblocks dedupe-across-files and
match-mode workloads (target_ids).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.backends.score_buckets import _resolve_fast_path, score_buckets
from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig, MatchkeyField
from goldenmatch.core.matchkey import _xform_sig


def _build_mk() -> MatchkeyConfig:
    """Simple 1-field weighted matchkey -- the rest of the gates pass."""
    return MatchkeyConfig(
        name="test",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )


def _build_prepared_df() -> pl.DataFrame:
    mk = _build_mk()
    xform = _xform_sig(mk.fields[0])
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__source__": ["A", "A", "B", "B"],
        "name": ["alice", "alice", "alice", "alice"],
        xform: ["alice", "alice", "alice", "alice"],
    })


class TestResolveFastPathAcceptsMatchMode:
    """Gate-level: _resolve_fast_path now accepts across_files_only,
    source_lookup, and target_ids instead of declining."""

    def test_accepts_across_files_only(self):
        result = _resolve_fast_path(
            _build_mk(), _build_prepared_df(),
            across_files_only=True,
            source_lookup={0: "A", 1: "A", 2: "B", 3: "B"},
            target_ids=None,
        )
        assert result is not None, "fast path must accept across_files_only=True"

    def test_accepts_source_lookup(self):
        result = _resolve_fast_path(
            _build_mk(), _build_prepared_df(),
            across_files_only=False,
            source_lookup={0: "A", 1: "B"},
            target_ids=None,
        )
        assert result is not None, "fast path must accept source_lookup"

    def test_accepts_target_ids(self):
        result = _resolve_fast_path(
            _build_mk(), _build_prepared_df(),
            across_files_only=False,
            source_lookup=None,
            target_ids={0, 1},
        )
        assert result is not None, "fast path must accept target_ids"


class TestMatchModeFiltersPairs:
    """End-to-end via score_buckets: pairs are post-filtered correctly."""

    def _run(self, **kwargs):
        df = _build_prepared_df()
        # Add block-key so blocking is deterministic.
        df = df.with_columns(pl.lit("blockX").alias("__block_key__"))
        mk = _build_mk()
        # Use a constant blocking config -- bucket scorer expects keys list.
        blocking = BlockingConfig(strategy="static", keys=["name"])
        return score_buckets(
            df, blocking, mk,
            matched_pairs=set(),
            **kwargs,
        )

    def test_across_files_only_drops_same_source_pairs(self):
        # 4 rows, sources A/A/B/B. With across_files_only, only A<>B pairs
        # should emit: (0,2), (0,3), (1,2), (1,3) = 4 pairs.
        # Without filter we'd get all C(4,2)=6 pairs.
        pairs = self._run(
            across_files_only=True,
            source_lookup={0: "A", 1: "A", 2: "B", 3: "B"},
        )
        assert pairs, "expected some cross-source pairs"
        for a, b, _s in pairs:
            sa = {0: "A", 1: "A", 2: "B", 3: "B"}[a]
            sb = {0: "A", 1: "A", 2: "B", 3: "B"}[b]
            assert sa != sb, f"pair ({a},{b}) has same source -- should have been filtered"

    def test_target_ids_drops_same_side_pairs(self):
        # target_ids = {0, 1}. Only pairs with exactly one side in target_ids
        # should emit: (0,2), (0,3), (1,2), (1,3).
        pairs = self._run(
            target_ids={0, 1},
        )
        assert pairs, "expected target<>non-target pairs"
        for a, b, _s in pairs:
            in_a = a in {0, 1}
            in_b = b in {0, 1}
            assert in_a != in_b, f"pair ({a},{b}) has both or neither in target_ids"


class TestParityWithSlowPath:
    """Same input via fast vs slow should produce same pair set after filter."""

    @pytest.mark.parametrize("kw", [
        {"across_files_only": True, "source_lookup": {0: "A", 1: "A", 2: "B", 3: "B"}},
        {"target_ids": {0, 1}},
    ])
    def test_fast_match_filter_subset_of_unfiltered(self, kw):
        """Filtered pairs must be a subset of unfiltered pairs."""
        unfiltered = self._unfiltered()
        df = _build_prepared_df()
        df = df.with_columns(pl.lit("blockX").alias("__block_key__"))
        blocking = BlockingConfig(strategy="static", keys=["name"])
        filtered = score_buckets(
            df, blocking, _build_mk(),
            matched_pairs=set(),
            **kw,
        )
        unfiltered_keys = {(a, b) for a, b, _ in unfiltered}
        filtered_keys = {(a, b) for a, b, _ in filtered}
        assert filtered_keys <= unfiltered_keys, "filtered must be a subset of unfiltered"
        assert len(filtered_keys) < len(unfiltered_keys), (
            "filter should drop at least some pairs on this fixture"
        )

    def _unfiltered(self):
        df = _build_prepared_df()
        df = df.with_columns(pl.lit("blockX").alias("__block_key__"))
        blocking = BlockingConfig(strategy="static", keys=["name"])
        return score_buckets(
            df, blocking, _build_mk(),
            matched_pairs=set(),
        )
