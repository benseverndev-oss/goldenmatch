"""Direct behavioral unit tests for the owned email kernels (Wave D1).

Complements the byte-parity harness in ``test_identifiers_parity.py`` (which
sweeps the committed ``tests/parity/identifiers_corpus.jsonl`` corpus against
both the pure-Python fallback and the native path) with a small set of
readable, self-documenting assertions pinned to the exact vectors used to
design the ``goldenflow-core::email`` Rust kernel.
"""
from __future__ import annotations

import polars as pl
from goldenflow.transforms.email import (
    email_extract_domain,
    email_lowercase,
    email_normalize,
    email_validate,
)


def _one(fn, value):
    return fn(pl.Series("v", [value])).to_list()[0]


def test_lowercase_trims_and_lowercases():
    assert _one(email_lowercase, " John@X.COM ") == "john@x.com"


def test_normalize_strips_gmail_dots_and_plus_tag():
    assert _one(email_normalize, "John.Doe+tag@Gmail.com") == "johndoe@gmail.com"


def test_normalize_strips_plus_tag_on_non_gmail():
    assert _one(email_normalize, "a+b@x.com") == "a@x.com"


def test_normalize_preserves_invalid_input_verbatim():
    assert _one(email_normalize, "notanemail") == "notanemail"


def test_normalize_lowercases_simple_address():
    assert _one(email_normalize, "A@B.com") == "a@b.com"


def test_extract_domain_lowercases_and_uses_last_at():
    assert _one(email_extract_domain, "x@Foo.COM") == "foo.com"


def test_extract_domain_none_without_at():
    assert _one(email_extract_domain, "noat") is None


def test_validate_valid_address():
    assert _one(email_validate, "a@b.co") is True


def test_validate_false_without_dot_in_domain():
    assert _one(email_validate, "a@b") is False


def test_validate_false_on_internal_whitespace():
    assert _one(email_validate, "a b@c.com") is False


def test_validate_false_on_double_at():
    assert _one(email_validate, "a@@b.com") is False


def test_validate_false_on_empty_string():
    assert _one(email_validate, "") is False


def test_validate_null_propagates():
    assert _one(email_validate, None) is None
