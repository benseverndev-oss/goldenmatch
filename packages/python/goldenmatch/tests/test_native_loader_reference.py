"""Reference-mode gate tests (docs/design/2026-07-01-rust-is-the-reference-roadmap.md).

Under GOLDENMATCH_NATIVE=auto, native runs wherever a component's kernel symbol
exists; pure-Python is the lossy fallback. _GATED_ON no longer governs auto.
"""
from __future__ import annotations

import pathlib
import re

from goldenmatch.core import _native_loader as nl


def _components_used_in_source() -> set[str]:
    root = pathlib.Path(nl.__file__).parent.parent  # goldenmatch/
    found: set[str] = set()
    for p in root.rglob("*.py"):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found |= set(re.findall(r'native_enabled\(\s*["\']([a-z_]+)["\']', text))
    return found


def test_every_component_has_a_disposition() -> None:
    """Every component passed to native_enabled() must be either native-capable
    (in _COMPONENT_SYMBOLS) or explicitly fallback-only -- no silent omissions
    (a missed component silently stays pure-Python under auto)."""
    for comp in _components_used_in_source():
        assert comp in nl._COMPONENT_SYMBOLS or comp in nl._FALLBACK_ONLY, comp


def test_has_symbol_false_for_unknown_component() -> None:
    assert nl._has_symbol("does_not_exist") is False


def test_auto_runs_native_for_byte_exact_ungated_components(monkeypatch) -> None:
    """pprl_bloom + perceptual are byte-exact; under reference-mode auto they run
    native whenever the symbol is present (no longer held behind _GATED_ON)."""

    class FakeNative:
        bloom_clk_batch = staticmethod(lambda *a, **k: None)
        perceptual_phash_image = staticmethod(lambda *a, **k: None)
        connected_components = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert nl.native_enabled("pprl_bloom") is True
    assert nl.native_enabled("perceptual") is True
    assert nl.native_enabled("clustering") is True


def test_auto_falls_back_when_symbol_absent(monkeypatch) -> None:
    """A wheel predating a symbol runs the honest fallback for that component."""

    class FakeNativeNoBloom:
        score_block_pairs_arrow = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNativeNoBloom)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert nl.native_enabled("pprl_bloom") is False  # symbol absent -> fallback
    assert nl.native_enabled("block_scoring") is True


def test_simhash_shares_the_sketch_kernel(monkeypatch) -> None:
    """simhash must not silently stay Python -- it uses the same byte-exact
    sketch kernel and must be native-capable when that symbol is present."""

    class FakeNative:
        sketch_simhash_band_hashes_batch = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert nl.native_enabled("simhash") is True
    assert nl.native_enabled("sketch") is True


def test_sail_scoring_is_native_capable_when_the_symbol_is_present(monkeypatch) -> None:
    """sail_scoring LEFT _FALLBACK_ONLY (2026-08-11).

    It was fallback-only because score_field_pairwise narrowed to f32, which
    changed match decisions at round thresholds. goldenmatch-native 0.1.21
    returns f64, so the divergence was removed at the source rather than
    tolerated, and the loader now treats it like any other component.

    NOTE the loader gate is SYMBOL presence, and the symbol exists on the old f32
    wheels too -- so this alone is not sufficient. The width check lives at the
    call site and is pinned by tests/test_sail_native_wheel_guard.py.
    """

    class FakeNative:
        score_field_pairwise = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.delenv("GOLDENMATCH_NATIVE", raising=False)
    assert nl.native_enabled("sail_scoring") is True


def test_fallback_only_is_empty_but_the_mechanism_survives() -> None:
    """Nothing is fallback-only today. The set must remain the place a proven
    divergence goes, so a future one is DECLARED rather than quietly enabled."""
    assert nl._FALLBACK_ONLY == frozenset()
    assert isinstance(nl._FALLBACK_ONLY, frozenset)


def test_env_zero_forces_fallback(monkeypatch) -> None:
    class FakeNative:
        bloom_clk_batch = staticmethod(lambda *a, **k: None)

    monkeypatch.setattr(nl, "_native", FakeNative)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    assert nl.native_enabled("pprl_bloom") is False


def test_env_one_requires_native(monkeypatch) -> None:
    monkeypatch.setattr(nl, "_native", None)
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "1")
    import pytest

    with pytest.raises(RuntimeError):
        nl.native_enabled("clustering")
