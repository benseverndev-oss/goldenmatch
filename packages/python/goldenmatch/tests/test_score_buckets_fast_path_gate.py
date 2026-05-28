"""Unit tests for ``_ne_effectively_empty`` and the broader fast-path gate
in ``score_buckets._resolve_fast_path``.

Background: at auto-config time on the QIS realistic shape (10M rows),
``promote_negative_evidence`` was adding an NE entry on the ``id`` column
with scorer name ``ensemble``. ``score_field`` doesn't implement
``ensemble`` and raises ``ValueError`` -- ``core/scorer.py::_NE_BROKEN``
catches this once per (scorer, field) and silently skips it on subsequent
calls. The NE contributes zero penalty at runtime.

But the old fast-path gate declined eligibility whenever
``mk.negative_evidence`` was truthy, regardless of whether the entries were
actually callable. That forced the entire workload onto the slow Python
``find_fuzzy_matches`` path and blocked the native + ExcludeSet handle
optimizations (PR #552). The smarter gate looks at the NE scorer names and
only declines when at least one would actually fire at runtime.
"""
from __future__ import annotations

import polars as pl
import pytest

from goldenmatch.backends.score_buckets import (
    _ne_effectively_empty,
    _resolve_fast_path,
)
from goldenmatch.config.schemas import (
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)


def _mk_with_ne(ne_entries: list[NegativeEvidenceField]) -> MatchkeyConfig:
    """A minimal weighted matchkey with the given NE entries -- the rest of
    the gate's prerequisites (threshold, weighted type, no rerank/llm) are
    satisfied so eligibility hinges on NE alone."""
    return MatchkeyConfig(
        name="weighted_test",
        type="weighted",
        threshold=0.7,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
        ],
        negative_evidence=ne_entries,
    )


class TestNeEffectivelyEmpty:
    def test_empty_ne_is_effectively_empty(self):
        mk = _mk_with_ne([])
        assert _ne_effectively_empty(mk) is True

    def test_none_ne_is_effectively_empty(self):
        mk = MatchkeyConfig(
            name="no_ne", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="a", scorer="jaro_winkler", weight=1.0)],
        )
        assert _ne_effectively_empty(mk) is True

    @pytest.mark.parametrize("broken_scorer", ["ensemble", "embedding", "record_embedding"])
    def test_broken_scorer_ne_is_effectively_empty(self, broken_scorer: str):
        """NE entries whose scorer score_field doesn't implement -- the
        _NE_BROKEN cache in core/scorer.py skips them at runtime, so they
        contribute zero penalty. The gate should treat NE as empty."""
        mk = _mk_with_ne([
            NegativeEvidenceField(field="id", scorer=broken_scorer, threshold=0.8, penalty=0.5),
        ])
        assert _ne_effectively_empty(mk) is True, (
            f"NE with broken scorer {broken_scorer!r} should be treated as empty"
        )

    @pytest.mark.parametrize("real_scorer", ["jaro_winkler", "levenshtein", "token_sort", "exact"])
    def test_callable_scorer_ne_is_not_empty(self, real_scorer: str):
        """NE entries with scorers score_field actually implements would
        contribute a real penalty at runtime. The gate must NOT treat
        these as empty -- the fast path would silently change the score."""
        mk = _mk_with_ne([
            NegativeEvidenceField(field="phone", scorer=real_scorer, threshold=0.8, penalty=0.5),
        ])
        assert _ne_effectively_empty(mk) is False, (
            f"NE with callable scorer {real_scorer!r} must NOT be treated as empty"
        )

    def test_mixed_broken_and_callable_is_not_empty(self):
        """Conservative: if ANY NE entry is callable, the whole set must be
        honored. The fast path is all-or-nothing on NE awareness."""
        mk = _mk_with_ne([
            NegativeEvidenceField(field="id", scorer="ensemble", threshold=0.8, penalty=0.5),
            NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.5),
        ])
        assert _ne_effectively_empty(mk) is False


class TestResolveFastPath:
    """End-to-end: with broken-scorer NE, _resolve_fast_path should return a
    spec (non-None). With callable NE, it should still return None."""

    def _prepared_df(self) -> pl.DataFrame:
        # Minimal prepared df with the xform columns the matchkey needs.
        # _xform_sig builds the column name from the field + transforms;
        # for plain field=X with no transforms it's just "__mk_X__".
        return pl.DataFrame({
            "__row_id__": [0, 1],
            "__mk_first_name__": ["alice", "alice"],
            "__mk_last_name__": ["smith", "smith"],
        })

    def test_fast_path_engaged_with_broken_ne(self):
        mk = _mk_with_ne([
            NegativeEvidenceField(field="id", scorer="ensemble", threshold=0.8, penalty=0.5),
        ])
        result = _resolve_fast_path(
            mk, self._prepared_df(),
            across_files_only=False, source_lookup=None, target_ids=None,
        )
        # _resolve_fast_path returns None when not eligible. With broken-NE
        # only, we should get the spec back. It's None ONLY if some other
        # prerequisite failed -- this fixture satisfies all of them, so a
        # None here would mean the broken-NE gate logic didn't fire.
        assert result is not None, (
            "broken-NE matchkey should be fast-path eligible after the gate fix"
        )

    def test_fast_path_declined_with_callable_ne(self):
        mk = _mk_with_ne([
            NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.5),
        ])
        result = _resolve_fast_path(
            mk, self._prepared_df(),
            across_files_only=False, source_lookup=None, target_ids=None,
        )
        assert result is None, (
            "callable-NE matchkey must still decline the fast path -- the "
            "fast path doesn't apply NE penalty math"
        )
