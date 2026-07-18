"""Unit tests for ``_ne_effectively_empty`` and the fast-path gate in
``score_buckets._resolve_fast_path``.

History (two layers):
1. The gate once declined the fast path whenever ``mk.negative_evidence`` was
   truthy -- too conservative, so the "smart gate" (PR #552) engaged when the NE
   scorers were runtime no-ops. Back then ``ensemble`` RAISED in ``score_field``,
   was cached in ``core/scorer.py::_NE_BROKEN``, and contributed zero penalty, so
   engaging + dropping it matched the slow path.
2. That premise is GONE: ``score_field`` later gained real handlers for
   ``ensemble`` / ``qgram`` / ``date`` / ``phash`` / ``audio_fp`` /
   ``initialism_match`` / ``alias_match``, so the SLOW path
   (``_apply_negative_evidence``) now APPLIES their penalty. But those scorers are
   still not in ``_SCORE_FIELD_DIRECT_SCORERS``, so the fast path dropped them ->
   zero penalty -> the SAME pair scored differently on bucket vs polars-direct.

The parity guard in ``_resolve_fast_path`` now DECLINES to the slow path for any
NE scorer outside ``_SCORE_FIELD_DIRECT_SCORERS`` (parity over speed; for a
genuinely-``_NE_BROKEN`` scorer the slow path also skips it, so declining is
correct either way). These tests pin that behavior.
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
        # _xform_sig builds `__xform_<field>_<blake2b-8hex>__` from the
        # field name + repr(field.transforms); compute it here so the
        # fixture stays in sync with the implementation. The original
        # comment claimed the column name was "__mk_X__" -- wrong; that
        # is the EXACT-matchkey concat alias, not the xform sig.
        from goldenmatch.config.schemas import MatchkeyField
        from goldenmatch.core.matchkey import _xform_sig
        col_first = _xform_sig(MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0))
        col_last = _xform_sig(MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0))
        return pl.DataFrame({
            "__row_id__": [0, 1],
            col_first: ["alice", "alice"],
            col_last: ["smith", "smith"],
        })

    @pytest.mark.parametrize("ne_scorer", ["ensemble", "qgram", "date"])
    def test_fast_path_declines_score_field_handled_but_non_direct_ne(self, ne_scorer):
        """PARITY GUARD: `score_field` gained handlers for ensemble/qgram/date/
        phash/audio_fp/initialism_match/alias_match, so the SLOW path
        (_apply_negative_evidence) applies their penalty -- but the fast path can
        only reproduce a per-pair penalty for _SCORE_FIELD_DIRECT_SCORERS and would
        silently DROP these (zero penalty). Same pair, different bucket-vs-polars
        score. The fast path must DECLINE so the reference slow path scores it.
        (Historically these RAISED -> _NE_BROKEN no-op -> engaging was safe; that
        premise is gone.)"""
        mk = _mk_with_ne([
            NegativeEvidenceField(field="id", scorer=ne_scorer, threshold=0.8, penalty=0.5),
        ])
        result = _resolve_fast_path(
            mk, self._prepared_df(),
            across_files_only=False, source_lookup=None, target_ids=None,
        )
        assert result is None, (
            f"NE scorer {ne_scorer!r} is applied by the slow path but not fast-"
            f"representable; the fast path must decline for parity, got engaged"
        )

    def test_fast_path_engaged_with_callable_ne_missing_xform(self):
        """Callable-NE on a field whose xform column isn't in prepared_df
        falls through silently (mirrors the slow path's `_NE_BROKEN` cache
        semantics: NE without computable inputs contributes zero penalty).

        Pre-2026-05-29 this declined the fast path outright. Now the gate
        engages and `_resolve_ne_specs` silently drops the unresolvable
        entry."""
        mk = _mk_with_ne([
            NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.5),
        ])
        result = _resolve_fast_path(
            mk, self._prepared_df(),
            across_files_only=False, source_lookup=None, target_ids=None,
        )
        assert result is not None, (
            "callable-NE on a missing-xform field should silently skip and "
            "engage the fast path (matches slow path's _NE_BROKEN semantics)"
        )
        # And: ne_specs should be empty since the NE entry was skipped.
        _, _, _, ne_specs = result
        assert ne_specs == [], (
            f"NE entry on missing-xform field should be silently dropped, "
            f"got ne_specs={ne_specs}"
        )

    def test_fast_path_engaged_with_callable_ne_present_xform(self):
        """Callable-NE on a field whose xform IS in prepared_df engages the
        fast path AND populates ne_specs with the per-pair penalty math
        plan. The bucket worker applies the penalty inline."""
        from goldenmatch.config.schemas import MatchkeyField
        from goldenmatch.core.matchkey import _xform_sig
        # Build a fixture that has the phone xform column too.
        phone_field = MatchkeyField(field="phone", scorer="jaro_winkler", weight=1.0)
        phone_col = _xform_sig(phone_field)
        prepared = self._prepared_df().with_columns(
            pl.Series(phone_col, ["555-1234", "555-9999"])
        )
        mk = _mk_with_ne([
            NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.5),
        ])
        result = _resolve_fast_path(
            mk, prepared,
            across_files_only=False, source_lookup=None, target_ids=None,
        )
        assert result is not None, "callable-NE with present xform should engage"
        _, _, _, ne_specs = result
        assert len(ne_specs) == 1, f"expected 1 resolved NE spec, got {ne_specs}"
        assert ne_specs[0][0] == phone_col
        assert ne_specs[0][2] == 0.8  # threshold
        assert ne_specs[0][3] == 0.5  # penalty
