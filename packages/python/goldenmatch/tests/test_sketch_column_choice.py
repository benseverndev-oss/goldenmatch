"""The blocking sketch column is chosen by identity, not by length (#2717).

`_auto_build_lsh_config` and friends picked `max(avg_len)` -- the LONGEST text
column. That is backwards: the longest free-text field is the most verbose and
the least identifying, because it shares boilerplate with every other row.

Measured on Abt-Buy, whose two text columns both profile as `col_type=description`:

    name         avg_len= 54.3  cardinality=0.994   <- identity lives here
    description  avg_len=178.5  cardinality=0.771   <- picked by max(avg_len)

Ground-truth blocking recall through the shipped blocker:

    lsh on 'description' (committed)   0.0009
    lsh on 'name'                      0.1139
    token on 'name' (max_df 0.02)      0.9198

Amazon-Google escaped this only because it carries a `manufacturer` column that
profiles as `col_type=name` with cardinality 0.162, which trips
`_is_text_corpus`'s blockable-name test and routes it away from the LSH branch
entirely. Luck, not design -- which is why this fixes the heuristic rather than
special-casing a dataset.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig import ColumnProfile, _best_sketch_column


def _prof(name: str, avg_len: float, cardinality: float,
          col_type: str = "description") -> ColumnProfile:
    return ColumnProfile(
        name=name, dtype="String", col_type=col_type, confidence=1.0,
        null_rate=0.0, cardinality_ratio=cardinality, avg_len=avg_len,
    )


def test_picks_the_identifying_column_not_the_longest():
    """The Abt-Buy shape, with its measured profile values."""
    profiles = [
        _prof("name", avg_len=54.3, cardinality=0.994),
        _prof("description", avg_len=178.5, cardinality=0.771),
    ]
    assert _best_sketch_column(profiles) == "name"


def test_length_still_breaks_a_cardinality_tie():
    """With nothing to choose on identity, the longer column carries more
    shingles for a sketch to work with, so the old preference stands."""
    profiles = [
        _prof("short", avg_len=20.0, cardinality=0.90),
        _prof("long", avg_len=200.0, cardinality=0.90),
    ]
    assert _best_sketch_column(profiles) == "long"


def test_a_near_unique_column_is_a_fine_sketch_target():
    """There is deliberately NO upper cardinality ceiling.

    The first version of this capped cardinality at 0.99, reasoning that a
    per-row-unique column blocks nothing because every value is its own block.
    That is true of an EXACT key and false of a sketch: LSH and token blocking
    group by shared shingles/tokens, not by equality. Abt-Buy's `name` is
    cardinality 0.994 -- the cap excluded the very column this function exists
    to pick, and this test failed until it was removed.
    """
    profiles = [
        _prof("blob", avg_len=500.0, cardinality=1.0),
        _prof("title", avg_len=50.0, cardinality=0.95),
    ]
    assert _best_sketch_column(profiles) == "blob"


def test_empty_input_returns_none_rather_than_raising():
    assert _best_sketch_column([]) is None


def test_amazon_google_shape_is_unchanged():
    """It never reaches this path (its `manufacturer` column routes it away),
    but if it ever did, `title` is still the right answer."""
    profiles = [
        _prof("title", avg_len=51.1, cardinality=0.978),
        _prof("description", avg_len=551.2, cardinality=0.899),
    ]
    assert _best_sketch_column(profiles) == "title"
