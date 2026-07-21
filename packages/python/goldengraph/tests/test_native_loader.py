"""Unit tests for the gated native-engine entry points.

The follow-up these lock in: runtime call sites used to hard-import the engine
ad-hoc (``graph.py::_new_store`` -> ``goldengraph_native._native``;
``profile.py`` -> ``goldenprofile_native.resolve_json``), so ``GOLDENGRAPH_NATIVE``
governed only the JSON parity surface, not the whole engine. Now both go through
``core._native_loader`` (``new_store`` / ``profile_resolve_json``), so the
force-disable contract (``=0`` -> clear error, no silent degrade) applies to the
store and the fingerprint-resolution sub-engine too.

The loader is pure-Python gate logic (no numpy, no wheel), so we load it by file
path under a synthetic module name -- the same bypass ``test_native_parity`` uses
to dodge ``goldengraph/__init__``'s heavy deps.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_LOADER_PATH = (
    Path(__file__).parent.parent / "goldengraph" / "core" / "_native_loader.py"
)


def _load_loader():
    """Import ``_native_loader.py`` in isolation (its top-level engine import is
    wrapped in try/except, so it degrades to ``_native = None`` with no wheel)."""
    spec = importlib.util.spec_from_file_location("_gg_loader_under_test", _LOADER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def loader():
    return _load_loader()


def test_force_disable_blocks_new_store(loader, monkeypatch):
    """``GOLDENGRAPH_NATIVE=0`` must raise (no pure-Python store fallback) rather
    than fall through to an ungated import."""
    monkeypatch.setenv("GOLDENGRAPH_NATIVE", "0")
    with pytest.raises(RuntimeError, match="force-disable"):
        loader.new_store()


def test_force_disable_blocks_profile_resolve(loader, monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_NATIVE", "0")
    with pytest.raises(RuntimeError, match="force-disable"):
        loader.profile_resolve_json()


def test_new_store_raises_clear_error_when_engine_absent(loader, monkeypatch):
    """With the engine unbuilt, the loader raises an actionable error naming the
    build path -- not an opaque ImportError/AttributeError at the call site."""
    monkeypatch.setenv("GOLDENGRAPH_NATIVE", "auto")
    monkeypatch.setattr(loader, "_native", None)
    with pytest.raises(RuntimeError, match="not built/importable"):
        loader.new_store()


def test_new_store_constructs_when_engine_present(loader, monkeypatch):
    """When a module exposing ``PyStore`` is present, ``new_store`` returns a
    fresh store from it (no direct wheel import at the call site)."""
    sentinel = object()

    class _FakeEngine:
        @staticmethod
        def PyStore():
            return sentinel

    monkeypatch.setenv("GOLDENGRAPH_NATIVE", "auto")
    monkeypatch.setattr(loader, "_native", _FakeEngine())
    assert loader.new_store() is sentinel


def test_require_native_new_store_raises_when_absent(loader, monkeypatch):
    """``=1`` (require-native) with no engine also raises the not-built error."""
    monkeypatch.setenv("GOLDENGRAPH_NATIVE", "1")
    monkeypatch.setattr(loader, "_native", None)
    with pytest.raises(RuntimeError, match="not built/importable"):
        loader.new_store()
