"""Direct behavioral unit tests for the owned URL kernels (Wave D2).

Complements the byte-parity harness in ``test_identifiers_parity.py`` (which
sweeps the committed ``tests/parity/identifiers_corpus.jsonl`` corpus against
both the pure-Python fallback and the native path) with a small set of
readable, self-documenting assertions pinned to the exact vectors used to
design the ``goldenflow-core::url`` Rust kernel.
"""
from __future__ import annotations

import polars as pl
from goldenflow.transforms.url import url_extract_domain, url_normalize


def _one(fn, value):
    return fn(pl.Series("v", [value])).to_list()[0]


def test_normalize_adds_scheme_and_lowercases_domain():
    assert _one(url_normalize, "Example.COM/Path/") == "https://example.com/Path"


def test_normalize_strips_single_trailing_slash_when_path_is_root():
    assert _one(url_normalize, "http://X.com/") == "http://x.com"


def test_normalize_leaves_no_trailing_slash_unchanged():
    assert _one(url_normalize, "https://a.com") == "https://a.com"


def test_normalize_strips_all_trailing_slashes_when_path_has_more():
    assert _one(url_normalize, "https://a.com/x/") == "https://a.com/x"
    assert _one(url_normalize, "https://a.com/x//") == "https://a.com/x"


def test_normalize_case_insensitive_scheme_detection():
    assert _one(url_normalize, "HTTPS://Foo.com") == "https://foo.com"
    assert _one(url_normalize, "HtTp://Foo.com") == "http://foo.com"


def test_normalize_empty_and_whitespace_are_none():
    assert _one(url_normalize, "") is None
    assert _one(url_normalize, "   ") is None


def test_normalize_null_propagates():
    assert _one(url_normalize, None) is None


def test_extract_domain_strips_scheme_and_lowercases():
    assert _one(url_extract_domain, "https://Foo.com/x") == "foo.com"


def test_extract_domain_no_scheme():
    assert _one(url_extract_domain, "bar.com") == "bar.com"


def test_extract_domain_multi_label():
    assert _one(url_extract_domain, "http://sub.domain.org/path/more") == "sub.domain.org"


def test_extract_domain_empty_and_whitespace_are_none():
    assert _one(url_extract_domain, "") is None
    assert _one(url_extract_domain, "   ") is None


def test_extract_domain_null_propagates():
    assert _one(url_extract_domain, None) is None
