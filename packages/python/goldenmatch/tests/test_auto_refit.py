"""Tests for ``dedupe_df(..., auto_refit=...)`` — the closed-loop refit wired
into the public entry point (D2, "Both, gated").

``auto_refit`` reads the corrections this run persisted to the configured
``MemoryStore`` (the LLM boost / review queue write ``approve``/``reject`` rows),
turns the confident approves into labels, and refits the Fellegi-Sunter model.
Suggest-only by default; ``"apply"`` re-runs scoring+clustering once with the
refined model. Corrections are pre-seeded here so the test is deterministic and
needs no model / network.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch._api import _resolve_auto_refit_mode, dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    MemoryConfig,
)
from goldenmatch.core.memory.store import Correction, MemoryStore


def _df():
    # 3 true-match pairs (0-1, 2-3, 4-5) sharing zip blocks.
    return pl.DataFrame({
        "first_name": ["John", "Jon", "Jane", "Jane", "Bob", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Doe", "Lee", "Lee"],
        "zip": ["111", "111", "222", "222", "333", "333"],
    })


def _probabilistic_mk():
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="last_name", scorer="jaro_winkler", levels=2, partial_threshold=0.85),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )


def _config(tmp_path, *, memory_enabled=True):
    return GoldenMatchConfig(
        matchkeys=[_probabilistic_mk()],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["zip"])]),
        memory=MemoryConfig(enabled=memory_enabled, path=str(tmp_path / "mem.db")),
    )


def _correction(id_a, id_b, decision, source="llm"):
    return Correction(
        id=f"{id_a}-{id_b}-{decision}", id_a=id_a, id_b=id_b, decision=decision,
        source=source, trust=0.5, field_hash="", record_hash="", original_score=0.8,
    )


def _seed(path, corrections):
    store = MemoryStore(backend="sqlite", path=str(path))
    for c in corrections:
        store.add_correction(c)
    store.close()


class TestResolveMode:
    def test_off(self):
        assert _resolve_auto_refit_mode(False) is None
        assert _resolve_auto_refit_mode(None) is None  # type: ignore[arg-type]

    def test_suggest(self):
        assert _resolve_auto_refit_mode(True) == "suggest"
        assert _resolve_auto_refit_mode("suggest") == "suggest"

    def test_apply(self):
        assert _resolve_auto_refit_mode("apply") == "apply"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _resolve_auto_refit_mode("nonsense")


class TestSuggestMode:
    def test_attaches_refit_suggestion(self, tmp_path):
        cfg = _config(tmp_path)
        _seed(cfg.memory.path, [
            _correction(0, 1, "approve"),
            _correction(2, 3, "approve"),
            _correction(4, 5, "approve"),
            _correction(0, 2, "reject"),  # ignored by the refit
        ])
        result = dedupe_df(_df(), config=cfg, auto_refit=True)
        assert result.refit_suggestion is not None
        assert result.refit_suggestion.n_labels == 3
        assert result.refit_suggestion.matchkey_name == "fs"

    def test_suggest_does_not_apply_model(self, tmp_path):
        cfg = _config(tmp_path)
        _seed(cfg.memory.path, [_correction(0, 1, "approve"), _correction(2, 3, "approve")])
        result = dedupe_df(_df(), config=cfg, auto_refit="suggest")
        assert result.refit_suggestion is not None
        # Suggest is side-effect-free: the run's config matchkey stays unrefined.
        assert result.config.get_matchkeys()[0].model_path is None


class TestApplyMode:
    def test_persists_and_reruns_with_refined_model(self, tmp_path):
        cfg = _config(tmp_path)
        _seed(cfg.memory.path, [
            _correction(0, 1, "approve"),
            _correction(2, 3, "approve"),
            _correction(4, 5, "approve"),
        ])
        result = dedupe_df(_df(), config=cfg, auto_refit="apply")
        # The suggestion is carried over...
        assert result.refit_suggestion is not None
        assert result.refit_suggestion.n_labels == 3
        # ...and the second pass ran with the refined model on disk.
        mk = result.config.get_matchkeys()[0]
        assert mk.model_path is not None
        from pathlib import Path
        assert Path(mk.model_path).exists()


class TestNoOp:
    def test_default_off_leaves_suggestion_none(self, tmp_path):
        cfg = _config(tmp_path)
        _seed(cfg.memory.path, [_correction(0, 1, "approve")])
        result = dedupe_df(_df(), config=cfg)
        assert result.refit_suggestion is None

    def test_memory_disabled_is_noop(self, tmp_path):
        cfg = _config(tmp_path, memory_enabled=False)
        result = dedupe_df(_df(), config=cfg, auto_refit=True)
        assert result.refit_suggestion is None

    def test_no_confident_labels_is_noop(self, tmp_path):
        cfg = _config(tmp_path)
        _seed(cfg.memory.path, [_correction(0, 1, "reject"), _correction(2, 3, "reject")])
        result = dedupe_df(_df(), config=cfg, auto_refit=True)
        assert result.refit_suggestion is None
