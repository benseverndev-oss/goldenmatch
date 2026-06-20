from __future__ import annotations

from goldenmatch.synonym.providers import (
    clear_synonym_models,
    register_synonym_model,
    resolve_synonym_model,
)


def test_default_is_stub_returns_none():
    clear_synonym_models()
    assert resolve_synonym_model("drug").score("a", "b") is None


def test_register_and_resolve():
    clear_synonym_models()

    class M:
        def score(self, a, b):
            return 0.5

    m = M()
    register_synonym_model("drug", m)
    assert resolve_synonym_model("drug") is m
    # unregistered domain -> stub (never raises)
    assert resolve_synonym_model("other").score("x", "y") is None
    clear_synonym_models()
