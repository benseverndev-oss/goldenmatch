"""Public ``goldenmatch.native`` surface.

Covers the top-level re-export and the ``string_similarity`` wrapper. Runs with
or without the compiled extension: both the native kernel and the rapidfuzz
fallback match rapidfuzz, so the assertions hold either way (the native-vs-fallback
bit-parity of the underlying kernels is locked separately in test_native_parity).
"""
from __future__ import annotations

import goldenmatch
import pytest
from goldenmatch import native
from rapidfuzz.distance import JaroWinkler, Levenshtein
from rapidfuzz.fuzz import token_sort_ratio


def test_native_is_reexported_at_top_level():
    assert "native" in goldenmatch.__all__
    assert goldenmatch.native is native


def test_native_exports_primitives_and_scorers():
    for name in ("canonicalize_pairs", "dedup_pairs_max_score", "connected_components",
                 "candidate_pair_count", "block_histogram", "available",
                 "string_similarity", "STRING_SCORERS"):
        assert name in native.__all__
        assert hasattr(native, name)


@pytest.mark.parametrize("a,b", [
    ("John Smith", "Jon Smyth"),
    ("Acme Corp", "Acme Corporation"),
    ("Smith John", "John Smith"),  # token reorder
    ("", ""),
    ("café", "cafe"),
])
def test_string_similarity_matches_rapidfuzz(a, b):
    assert native.string_similarity(a, b, "jaro_winkler") == pytest.approx(
        JaroWinkler.similarity(a, b), abs=1e-9)
    assert native.string_similarity(a, b, "levenshtein") == pytest.approx(
        Levenshtein.normalized_similarity(a, b), abs=1e-9)
    assert native.string_similarity(a, b, "token_sort") == pytest.approx(
        token_sort_ratio(a, b) / 100.0, abs=1e-9)


def test_string_similarity_range_and_self():
    for scorer in native.STRING_SCORERS:
        s = native.string_similarity("hello world", "hello world", scorer)
        assert s == pytest.approx(1.0, abs=1e-9)
        assert 0.0 <= native.string_similarity("abc", "xyz", scorer) <= 1.0


def test_string_similarity_handles_none():
    assert native.string_similarity(None, "x", "jaro_winkler") == pytest.approx(
        native.string_similarity("", "x", "jaro_winkler"))


def test_string_similarity_rejects_unknown_scorer():
    with pytest.raises(ValueError, match="scorer must be one of"):
        native.string_similarity("a", "b", "cosine")


def test_string_similarity_fallback_matches_native(monkeypatch):
    # Force the pure-Python path; result must equal the default (native when built).
    default = native.string_similarity("Margaret Chen", "Maggie Chen", "jaro_winkler")
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    forced_python = native.string_similarity("Margaret Chen", "Maggie Chen", "jaro_winkler")
    assert forced_python == pytest.approx(default, abs=1e-9)
