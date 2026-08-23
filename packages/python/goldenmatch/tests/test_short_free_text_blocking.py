"""Short free text blocks with `token`, documents with `lsh` (#2717).

The text-corpus branch committed MinHash/LSH for anything it reached, and
`blocking.mdx` already prescribed otherwise: "Use `lsh` for near-duplicate
documents, `token` for short free text where only a few tokens agree." A
product title is the second case, not the first.

Ground-truth blocking recall on Abt-Buy through the shipped blocker, per column
and strategy:

    lsh   on 'name'        (thr 0.5)          0.1139   5,082 comparisons
    lsh   on 'name'        (thr 0.2)          0.5123  64,466
    token on 'name'        (max_df 0.02)      0.9198  64,056
    lsh   on 'description' (thr 0.5)          0.0009  13,071
    token on 'description' (max_df 0.02)      0.3801 137,720

At equal cost -- ~64k comparisons -- token nearly doubles LSH's recall on the
same column.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig import (
    _SHORT_FREE_TEXT_MAX_LEN,
    ColumnProfile,
    _text_corpus_blocking,
)


def _prof(name: str, avg_len: float, cardinality: float) -> ColumnProfile:
    return ColumnProfile(
        name=name, dtype="String", col_type="description", confidence=1.0,
        null_rate=0.0, cardinality_ratio=cardinality, avg_len=avg_len,
    )


def test_abt_buy_shape_routes_to_token_on_the_identifying_column():
    """The measured case: product names, 54 chars, the highest-cardinality
    column of the two."""
    blocking = _text_corpus_blocking([
        _prof("name", avg_len=54.3, cardinality=0.994),
        _prof("description", avg_len=178.5, cardinality=0.771),
    ])
    assert blocking.strategy == "token"
    assert blocking.token is not None
    assert blocking.token.column == "name"


def test_a_long_document_column_still_gets_lsh():
    """LSH's designed case -- near-duplicate documents -- is untouched. A
    web-crawl corpus is hundreds of characters per row, not fifty."""
    blocking = _text_corpus_blocking([
        _prof("body", avg_len=3200.0, cardinality=0.999),
    ])
    assert blocking.strategy == "lsh"
    assert blocking.lsh is not None
    assert blocking.lsh.column == "body"


def test_the_bar_is_read_off_the_chosen_column_not_the_longest():
    """Column choice happens FIRST, then the strategy follows from THAT
    column's length. Reading the bar off the longest column would send the
    Abt-Buy shape to LSH again via the back door."""
    profiles = [
        _prof("name", avg_len=54.3, cardinality=0.994),
        _prof("description", avg_len=900.0, cardinality=0.771),
    ]
    blocking = _text_corpus_blocking(profiles)
    assert blocking.strategy == "token"
    assert blocking.token.column == "name"


def test_the_boundary_is_inclusive_and_documented():
    """Pin the constant so a future change to it is a deliberate act."""
    short = _text_corpus_blocking([_prof("c", _SHORT_FREE_TEXT_MAX_LEN, 0.9)])
    long_ = _text_corpus_blocking([_prof("c", _SHORT_FREE_TEXT_MAX_LEN + 1, 0.9)])
    assert short.strategy == "token"
    assert long_.strategy == "lsh"


def test_an_unmeasured_column_keeps_the_document_route():
    """`avg_len` 0 means UNKNOWN, not short.

    A hand-built `ColumnProfile` defaults `avg_len` to 0. Reading that as
    "short" would silently move every caller that constructs profiles directly
    rather than measuring them -- which is how `test_autoconfig.py` builds them,
    and how it caught this.
    """
    blocking = _text_corpus_blocking([_prof("desc", avg_len=0.0, cardinality=0.7)])
    assert blocking.strategy == "lsh"
