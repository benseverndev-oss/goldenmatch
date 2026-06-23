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


def test_apply_default_on_routes_to_simhash(embedder_on, monkeypatch):
    # #1090: default ON. Text-heavy data + a reachable embedder -> simhash with
    # no env flag set.
    monkeypatch.delenv("GOLDENMATCH_AUTO_SEMANTIC_BLOCKING", raising=False)
    out = apply_auto_semantic_blocking(_static(), _text_heavy_profiles())
    assert out.strategy == "simhash"
    assert out.simhash.column == "description"


def test_apply_default_on_noop_without_embedder(embedder_off, monkeypatch):
    # Default ON is still a no-op when no embedder is reachable -> a user without
    # the in-house model / a provider sees byte-identical output.
    monkeypatch.delenv("GOLDENMATCH_AUTO_SEMANTIC_BLOCKING", raising=False)
    cfg = _static()
    assert apply_auto_semantic_blocking(cfg, _text_heavy_profiles()) is cfg


def test_apply_env_zero_disables(embedder_on, monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTO_SEMANTIC_BLOCKING", "0")
    cfg = _static()
    assert apply_auto_semantic_blocking(cfg, _text_heavy_profiles()) is cfg


def test_recall_threshold_drives_band_split(embedder_on, monkeypatch):
    # #1090: the recall threshold (not a hardcoded num_bands) shapes the simhash
    # config; the env override is honored and reaches the committed config.
    monkeypatch.delenv("GOLDENMATCH_AUTO_SEMANTIC_BLOCKING", raising=False)
    monkeypatch.setenv("GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD", "0.8")
    out = apply_auto_semantic_blocking(_static(), _text_heavy_profiles())
    assert out.simhash.threshold == 0.8
    assert out.simhash.num_bands is None  # threshold-driven, not hardcoded bands


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


# ── native = source of truth: the SimHash kernel runs native by default (#1090) ─


def test_sketch_kernel_is_default_on_native():
    """The SimHash band-hashing kernel (sketch-core) is the runtime source of
    truth: ``"sketch"`` is in the default-on native allowlist, so semantic
    blocking dispatches to Rust by default wherever the wheel is present."""
    from goldenmatch.core import _native_loader as nl

    assert "sketch" in nl._GATED_ON


def test_sketch_native_byte_identical_to_python():
    """The native kernel and the pure-Python reference produce identical band
    hashes -- the parity the default-on flip rests on. Skips when the native
    wheel isn't built (CI's default lane falls back to Python, still correct)."""
    import numpy as np
    from goldenmatch.core import sketch
    from goldenmatch.core._native_loader import native_available

    if not native_available():
        pytest.skip("goldenmatch._native not built in this lane (Python fallback path)")

    rng = np.random.default_rng(7)
    vecs = [rng.standard_normal(128).tolist() for _ in range(256)]
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("GOLDENMATCH_NATIVE", "0")
        py = sketch.simhash_band_hashes_batch(vecs, 128, 16, 42)
        monkey.setenv("GOLDENMATCH_NATIVE", "1")
        nat = sketch.simhash_band_hashes_batch(vecs, 128, 16, 42)
    finally:
        monkey.undo()
    assert py == nat
