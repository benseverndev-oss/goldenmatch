"""Tests for the closed-loop refit (D2, suggest mode).

The borderline-band adjudicator (LLM boost or human review) produces per-pair
verdicts; this loop turns the CONFIDENT ones into labels and feeds the existing
supervised primitive ``estimate_m_from_labels`` to produce a refined ``EMResult``
returned as a suggestion. No model / network needed — labels are supplied
directly here.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core import refit as R


def _make_probabilistic_mk(**kwargs):
    defaults = dict(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    defaults.update(kwargs)
    return MatchkeyConfig(**defaults)


def _supervised_df():
    # 3 true-match pairs (0-1, 2-3, 4-5) sharing zip blocks.
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3, 4, 5],
        "first_name": ["John", "Jon", "Jane", "Jane", "Bob", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Doe", "Lee", "Lee"],
        "zip": ["111", "111", "222", "222", "333", "333"],
    })


def _blocking():
    return BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])])


def _config():
    return GoldenMatchConfig(matchkeys=[_make_probabilistic_mk()], blocking=_blocking())


class TestLabelsFromVerdicts:
    def test_keeps_confident_matches_only(self):
        verdicts = [
            (0, 1, True, 0.99),   # confident match -> label
            (2, 3, True, 0.80),   # match but below threshold -> dropped
            (4, 5, False, 0.99),  # confident NON-match -> dropped (u is from random pairs)
        ]
        labels = R.labels_from_verdicts(verdicts, confidence_threshold=0.95)
        assert labels == [(0, 1)]

    def test_canonicalizes_and_dedupes(self):
        verdicts = [(1, 0, True, 0.99), (0, 1, True, 0.97)]
        labels = R.labels_from_verdicts(verdicts, confidence_threshold=0.95)
        assert labels == [(0, 1)]

    def test_threshold_boundary_is_inclusive(self):
        verdicts = [(0, 1, True, 0.95)]
        assert R.labels_from_verdicts(verdicts, confidence_threshold=0.95) == [(0, 1)]


def _correction(id_a, id_b, decision, source="llm"):
    from goldenmatch.core.memory.store import Correction
    return Correction(
        id=f"{id_a}-{id_b}-{decision}", id_a=id_a, id_b=id_b, decision=decision,
        source=source, trust=0.5, field_hash="", record_hash="", original_score=0.8,
    )


class TestLabelsFromCorrections:
    def test_keeps_approve_pairs_only(self):
        corrections = [
            _correction(0, 1, "approve"),          # -> label
            _correction(2, 3, "reject"),           # dropped (u is from random pairs)
            _correction(9, 0, "field_correct"),    # dropped (id_a is a cluster_id, not a pair)
            _correction(4, 4, "approve"),          # dropped (degenerate self-pair)
        ]
        assert R.labels_from_corrections(corrections) == [(0, 1)]

    def test_canonicalizes_and_dedupes(self):
        corrections = [_correction(1, 0, "approve"), _correction(0, 1, "approve")]
        assert R.labels_from_corrections(corrections) == [(0, 1)]

    def test_source_filter(self):
        corrections = [
            _correction(0, 1, "approve", source="llm"),
            _correction(2, 3, "approve", source="boost"),
        ]
        assert R.labels_from_corrections(corrections, sources={"llm"}) == [(0, 1)]

    def test_refit_from_memory_reads_store(self, tmp_path):
        from goldenmatch.core.memory.store import MemoryStore
        store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
        for a, b in [(0, 1), (2, 3), (4, 5)]:
            store.add_correction(_correction(a, b, "approve"))
        store.add_correction(_correction(0, 2, "reject"))  # ignored by the refit

        result = R.refit_from_memory(_supervised_df(), _config(), store)
        assert result.n_labels == 3
        assert result.matchkey_name == "fs"
        assert result.em_result.iterations == 0


class TestRefitFromLabels:
    def test_returns_refined_em_and_thresholds(self):
        df, config = _supervised_df(), _config()
        result = R.refit_from_labels(df, config, [(0, 1), (2, 3), (4, 5)])
        assert result.n_labels == 3
        assert result.matchkey_name == "fs"
        assert result.em_result.iterations == 0            # supervised, no EM
        assert result.em_result.converged is True
        assert 0.0 <= result.review_threshold <= result.link_threshold <= 1.0

    def test_suggest_mode_does_not_mutate_config(self):
        df, config = _supervised_df(), _config()
        R.refit_from_labels(df, config, [(0, 1), (2, 3), (4, 5)])
        assert config.get_matchkeys()[0].model_path is None  # suggestion, side-effect-free

    def test_refit_from_verdicts_convenience(self):
        df, config = _supervised_df(), _config()
        verdicts = [(0, 1, True, 0.99), (2, 3, True, 0.99), (4, 5, True, 0.99)]
        result = R.refit_from_verdicts(df, config, verdicts, confidence_threshold=0.95)
        assert result.n_labels == 3

    def test_no_probabilistic_matchkey_raises(self):
        df = _supervised_df()
        weighted = MatchkeyConfig(
            name="w", type="weighted",
            fields=[MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0, threshold=0.8)],
            threshold=0.8,
        )
        config = GoldenMatchConfig(matchkeys=[weighted], blocking=_blocking())
        with pytest.raises(R.RefitNotApplicableError):
            R.refit_from_labels(df, config, [(0, 1)])

    def test_no_confident_labels_raises(self):
        df, config = _supervised_df(), _config()
        with pytest.raises(R.RefitNotApplicableError):
            R.refit_from_verdicts(df, config, [(0, 1, True, 0.5)], confidence_threshold=0.95)


class TestApply:
    def test_persist_writes_model_and_points_matchkey(self, tmp_path):
        df, config = _supervised_df(), _config()
        result = R.refit_from_labels(df, config, [(0, 1), (2, 3), (4, 5)])
        path = str(tmp_path / "refined.json")

        suggested = result.persist(config, path)

        # The refined model is on disk and reloadable...
        from goldenmatch.core.probabilistic import EMResult
        reloaded = EMResult.load_json(path)
        assert reloaded.match_weights.keys() == result.em_result.match_weights.keys()
        # ...the suggested config points at it...
        assert suggested.get_matchkeys()[0].model_path == path
        # ...and the ORIGINAL config is untouched (apply returns a copy).
        assert config.get_matchkeys()[0].model_path is None
