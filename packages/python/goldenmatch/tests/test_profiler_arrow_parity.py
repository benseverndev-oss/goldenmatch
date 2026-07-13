"""Arrow-vs-Polars parity for ``profiler.profile_dataframe`` (autoconfig arrow-port PR-2).

``profile_dataframe`` accepts either backend via the Frame seam. This pins that a
``PolarsFrame``-backed and an ``ArrowFrame``-backed input built from the SAME data
produce an identical report dict -- exercising duplicate-row counting, all-empty-row
counting (null / whitespace / zero-not-empty), mixed string+int+bool columns, and
nulls. The polars path's byte-parity is proven separately by the UNEDITED
``tests/test_profiler.py``.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
from goldenmatch.core.frame import to_frame
from goldenmatch.core.profiler import profile_dataframe


def _cases() -> dict[str, dict]:
    return {
        # duplicate full rows + nulls + a low-card col
        "dupes_and_nulls": {
            "email": ["a@b.com", "a@b.com", "c@d.com", None, "a@b.com"],
            "count": [1, 1, 2, None, 1],
            "flag": [True, True, False, None, True],
        },
        # all-empty rows: full-null row, null+whitespace row; a zero-int row is NOT empty
        "empty_rows": {
            "name": ["John", None, "  ", "0", None],
            "note": ["hi", None, None, None, "  "],
            "num": [5, None, None, 0, None],
        },
        # heavy nulls / whitespace mix + bool column
        "mixed": {
            "s": ["x", "", "  ", None, "y", "y"],
            "n": [10, 20, None, 30, 10, 10],
            "b": [True, False, None, True, True, True],
        },
    }


def test_profile_dataframe_arrow_polars_identical():
    for label, data in _cases().items():
        pl_frame = to_frame(pl.DataFrame(data))
        arrow_frame = to_frame(pa.table(data))

        pl_profile = profile_dataframe(pl_frame)
        arrow_profile = profile_dataframe(arrow_frame)

        assert pl_profile == arrow_profile, f"profile mismatch on case {label!r}"


def test_profile_dataframe_accepts_raw_backends():
    """Both raw ``pl.DataFrame`` and raw ``pa.Table`` are accepted (idempotent to_frame)."""
    data = _cases()["mixed"]
    from_pl = profile_dataframe(pl.DataFrame(data))
    from_arrow = profile_dataframe(pa.table(data))
    assert from_pl == from_arrow


def test_empty_row_semantics_zero_not_empty():
    """A row whose only non-null cell is a numeric 0 is NOT an empty row."""
    data = {"a": [None, None], "b": [0, None]}
    prof = profile_dataframe(pa.table(data))
    # row 0: a=null, b=0 -> NOT empty; row 1: both null -> empty
    assert prof["empty_row_count"] == 1
