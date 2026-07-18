"""Regression: the bench-dump candidate-pair accounting must read __row_id__
arrow-safely. On the (now-default) arrow lane block_df is a pa.Table whose
``["__row_id__"]`` is a ChunkedArray (no ``.to_list()``), which broke the
bench-probabilistic panel's goldenmatch F1 with an AttributeError. The Frame
seam normalizes both reps.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
from goldenmatch.core.pipeline import _accumulate_block_candidate_pairs


def test_accumulate_candidate_pairs_arrow_matches_polars():
    ids = [3, 1, 2]
    expected = {(1, 2), (1, 3), (2, 3)}  # canonical (min,max) within-block pairs

    s_pl: set = set()
    _accumulate_block_candidate_pairs(pl.DataFrame({"__row_id__": ids, "x": list("abc")}), s_pl)
    assert s_pl == expected

    # pa.Table lane: was AttributeError('ChunkedArray' has no 'to_list') pre-fix.
    s_pa: set = set()
    _accumulate_block_candidate_pairs(pa.table({"__row_id__": ids, "x": list("abc")}), s_pa)
    assert s_pa == expected
