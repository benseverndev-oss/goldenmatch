"""Auto-enabled semantic blocking (#1090).

Door pattern (mirrors quality-aware blocking): default OFF
(``GOLDENMATCH_AUTO_SEMANTIC_BLOCKING``), additive, and honest -- text-heavy data
routes to SimHash-over-embeddings ONLY when an embedder is reachable, otherwise a
no-op. Embedder availability is monkeypatched so these run offline + deterministic.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    SimHashKeyConfig,
)
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    apply_auto_semantic_blocking,
    decide_semantic_blocking,
)


def _text_heavy_profiles():
    return [
        ColumnProfile(name="sku", dtype="str", col_type="identifier",
                      confidence=1.0, avg_len=8.0, cardinality_ratio=0.9),
        ColumnProfile(name="description", dtype="str", col_type="description",
                      confidence=1.0, avg_len=120.0),
        ColumnProfile(name="summary", dtype="str", col_type="string",
                      confidence=1.0, avg_len=60.0),
    ]


def _short_profiles():
    return [
        ColumnProfile(name="name", dtype="str", col_type="name",
                      confidence=1.0, avg_len=12.0),
        ColumnProfile(name="city", dtype="str", col_type="string",
                      confidence=1.0, avg_len=9.0),
    ]


def _static(col="sku"):
    return BlockingConfig(strategy="static", keys=[BlockingKeyConfig(fields=[col])])


@pytest.fixture()
def embedder_on(monkeypatch):
    monkeypatch.setattr(
        "goldenmatch.core.autoconfig._embedder_available", lambda config=None: True,
    )


@pytest.fixture()
def embedder_off(monkeypatch):
    monkeypatch.setattr(
        "goldenmatch.core.autoconfig._embedder_available", lambda config=None: False,
    )


# ── decide_semantic_blocking ────────────────────────────────────────────────


def test_decide_disabled_is_off(embedder_on):
    d = decide_semantic_blocking(_text_heavy_profiles(), enabled=False)
    assert d.enabled is False
    assert d.reason == "disabled"


def test_decide_text_heavy_with_embeddings_picks_longest(embedder_on):
    d = decide_semantic_blocking(_text_heavy_profiles(), enabled=True)
    assert d.enabled is True
    assert d.column == "description"  # longest text-heavy column
    assert d.reason == "text_heavy_with_embeddings"
    assert d.embeddings_available is True


def test_decide_not_text_heavy_is_off(embedder_on):
    d = decide_semantic_blocking(_short_profiles(), enabled=True)
    assert d.enabled is False
    assert d.reason == "not_text_heavy"


def test_decide_honest_fallback_without_embeddings(embedder_off):
    d = decide_semantic_blocking(_text_heavy_profiles(), enabled=True)
    assert d.enabled is False
    assert d.reason == "embeddings_unavailable"
    assert d.column == "description"  # detected, but not committed
    assert d.embeddings_available is False


# ── apply_auto_semantic_blocking ────────────────────────────────────────────


def test_apply_default_off_is_noop(embedder_on, monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_AUTO_SEMANTIC_BLOCKING", raising=False)
    cfg = _static()
    out = apply_auto_semantic_blocking(cfg, _text_heavy_profiles())
    assert out is cfg  # unchanged object -> byte-identical default behaviour


def test_apply_routes_to_simhash_when_enabled(embedder_on):
    out = apply_auto_semantic_blocking(_static(), _text_heavy_profiles(), enabled=True)
    assert out.strategy == "simhash"
    assert out.simhash is not None
    assert out.simhash.column == "description"


def test_apply_honest_fallback_keeps_scheme(embedder_off):
    cfg = _static()
    out = apply_auto_semantic_blocking(cfg, _text_heavy_profiles(), enabled=True)
    assert out is cfg  # no embedder -> lexical/structured scheme stands


def test_apply_never_overrides_already_semantic(embedder_on):
    cfg = BlockingConfig(
        strategy="simhash",
        simhash=SimHashKeyConfig(column="description", num_planes=256,
                                 num_bands=32, seed=0),
    )
    out = apply_auto_semantic_blocking(cfg, _text_heavy_profiles(), enabled=True)
    assert out is cfg


def test_apply_not_text_heavy_is_noop(embedder_on):
    cfg = _static("name")
    out = apply_auto_semantic_blocking(cfg, _short_profiles(), enabled=True)
    assert out is cfg


def test_apply_handles_none_blocking(embedder_on):
    out = apply_auto_semantic_blocking(None, _text_heavy_profiles(), enabled=True)
    assert out is not None
    assert out.strategy == "simhash"
    assert out.simhash.column == "description"


def test_apply_disabled_returns_none_blocking_unchanged(embedder_on):
    assert apply_auto_semantic_blocking(None, _text_heavy_profiles(), enabled=False) is None
