"""Negative-evidence penalty math on the fast path (2026-05-29).

Pre-this-PR: any NE entry with a callable scorer declined fast-path
eligibility, forcing find_fuzzy_matches. Now `_resolve_ne_specs` resolves
the NE entries to per-pair penalty callables and the Python fast path
applies `combined = max(0, combined - sum(penalties))` inline.

Formula source: core/scorer.py `_apply_negative_evidence`. Same semantics.

Native kernel: skipped when ne_specs is non-empty -- it threshold-filters
inside the kernel pre-penalty, so we'd need to teach it to emit pre-penalty
candidates (~2x emit volume) to keep using it. Not yet justified.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.backends.score_buckets import (
    _resolve_fast_path,
    _resolve_ne_specs,
    score_buckets,
)
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)
from goldenmatch.core.matchkey import _xform_sig


def _mk(ne_entries: list[NegativeEvidenceField] | None = None) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="t",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
        negative_evidence=ne_entries or [],
    )


def _prepared(name_vals, phone_vals):
    mk = _mk()
    name_col = _xform_sig(mk.fields[0])
    phone_col = _xform_sig(
        MatchkeyField(field="phone", scorer="jaro_winkler", weight=1.0)
    )
    return pl.DataFrame({
        "__row_id__": list(range(len(name_vals))),
        "name": name_vals,
        "phone": phone_vals,
        name_col: name_vals,
        phone_col: phone_vals,
    })


class TestResolveNeSpecs:
    def test_empty_ne_returns_empty(self):
        df = _prepared(["a", "a"], ["1", "1"])
        assert _resolve_ne_specs(_mk(), df) == []

    def test_broken_scorer_silently_skipped(self):
        """ensemble / embedding scorers aren't in _SCORE_FIELD_DIRECT_SCORERS;
        mirror the slow path's _NE_BROKEN behavior and silently drop them."""
        df = _prepared(["a", "a"], ["1", "1"])
        mk = _mk([NegativeEvidenceField(field="phone", scorer="ensemble", threshold=0.8, penalty=0.5)])
        assert _resolve_ne_specs(mk, df) == []

    def test_missing_xform_silently_skipped(self):
        """NE on a field whose xform column isn't precomputed should drop
        silently -- can't compute, treat as broken."""
        df = pl.DataFrame({"__row_id__": [0, 1], "name": ["a", "a"]})
        mk = _mk([NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.5)])
        assert _resolve_ne_specs(mk, df) == []

    def test_resolvable_ne_produces_spec(self):
        df = _prepared(["a", "a"], ["555-1234", "555-9999"])
        mk = _mk([NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.4)])
        specs = _resolve_ne_specs(mk, df)
        assert len(specs) == 1
        xform_col, fn, threshold, penalty = specs[0]
        assert xform_col == _xform_sig(MatchkeyField(field="phone", scorer="jaro_winkler", weight=1.0))
        assert callable(fn)
        assert threshold == 0.8
        assert penalty == 0.4


class TestFastPathEngagedWithNe:
    def test_gate_accepts_resolvable_ne(self):
        df = _prepared(["alice", "alice"], ["555-1234", "555-9999"])
        mk = _mk([NegativeEvidenceField(field="phone", scorer="jaro_winkler", threshold=0.8, penalty=0.5)])
        result = _resolve_fast_path(
            mk, df,
            across_files_only=False, source_lookup=None, target_ids=None,
        )
        assert result is not None, "fast path must engage with resolvable NE"
        _, _, field_specs, ne_specs = result
        assert len(field_specs) == 1
        assert len(ne_specs) == 1


class TestPerPairNePenalty:
    """End-to-end: an NE field that disagrees on a candidate pair drops the
    final score below threshold and the pair is filtered out."""

    def _run(self, name_vals, phone_vals, ne_penalty=0.5):
        df = _prepared(name_vals, phone_vals).with_columns(
            pl.lit("blockX").alias("__block_key__")
        )
        mk = _mk([NegativeEvidenceField(
            field="phone", scorer="jaro_winkler",
            threshold=0.9, penalty=ne_penalty,
        )])
        blocking = BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["name"])]
        )
        return score_buckets(df, blocking, mk, matched_pairs=set())

    def test_ne_agreement_keeps_pair(self):
        """When phone matches (NE sim >= threshold), no penalty -- pair survives."""
        pairs = self._run(["alice", "alice"], ["555-1234", "555-1234"])
        assert pairs, "matching phone -> no penalty -> pair must survive"

    def test_ne_disagreement_drops_pair(self):
        """When phone differs strongly (NE sim < threshold), penalty subtracts
        from score. With penalty=1.0 and base score 1.0, final = 0 < threshold."""
        pairs = self._run(
            ["alice", "alice"],
            ["555-1234", "999-9999"],
            ne_penalty=1.0,  # large enough to push final below threshold
        )
        assert pairs == [], (
            f"NE disagreement with penalty=1.0 must drop the pair below "
            f"threshold; got {pairs}"
        )

    def test_ne_partial_penalty_reduces_but_keeps(self):
        """Small penalty drops the score but stays above threshold."""
        pairs = self._run(
            ["alice", "alice"],
            ["555-1234", "999-9999"],
            ne_penalty=0.1,  # small enough that 1.0 - 0.1 = 0.9 >= threshold 0.5
        )
        assert pairs, "small NE penalty should not push pair below threshold"
        # Score should be reduced from base 1.0 by exactly 0.1.
        assert pytest.approx(pairs[0][2], abs=0.001) == 0.9


class TestParityWithSlowPath:
    """Sanity: fast-path-with-NE-math output equals slow-path output on
    representative shapes. The slow path is the source of truth."""

    def test_parity_phone_disagree(self):
        from goldenmatch.config.schemas import BlockingConfig as _BC
        from goldenmatch.core.scorer import find_fuzzy_matches

        df = _prepared(["alice", "alice"], ["555-1234", "999-9999"]).with_columns(
            pl.lit("blockX").alias("__block_key__")
        )
        mk = _mk([NegativeEvidenceField(
            field="phone", scorer="jaro_winkler",
            threshold=0.9, penalty=0.3,
        )])
        blocking = _BC(strategy="static", keys=[BlockingKeyConfig(fields=["name"])])
        fast_pairs = score_buckets(df, blocking, mk, matched_pairs=set())
        slow_pairs = find_fuzzy_matches(df, mk, exclude_pairs=frozenset(), pre_scored_pairs=None)
        fast_keys = sorted((min(a, b), max(a, b)) for a, b, _ in fast_pairs)
        slow_keys = sorted((min(a, b), max(a, b)) for a, b, _ in slow_pairs)
        assert fast_keys == slow_keys, (
            f"fast vs slow pair-set mismatch:\n  fast={fast_pairs}\n  slow={slow_pairs}"
        )
        for (fa, fb, fs), (sa, sb, ss) in zip(
            sorted(fast_pairs), sorted(slow_pairs)
        ):
            assert (fa, fb) == (sa, sb)
            assert pytest.approx(fs, abs=0.01) == ss, (
                f"score mismatch at ({fa},{fb}): fast={fs} slow={ss}"
            )
