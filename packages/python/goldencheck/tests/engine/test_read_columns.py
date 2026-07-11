from __future__ import annotations

from datetime import date

import polars as pl
import pyarrow.parquet as pq
from goldencheck.engine.reader import _read_parquet_columns


def _write_parquet(tmp_path):
    import pyarrow as pa
    tbl = pa.table({
        "i": [1, 2, None, 4],
        "f": [1.5, 2.5, 3.5, None],
        "s": ["a", "b", None, "d"],
        "b": [True, False, True, None],
        "d": [date(2021, 1, 5), date(2021, 2, 6), None, date(2021, 3, 7)],
    })
    p = tmp_path / "f.parquet"
    pq.write_table(tbl, p)
    return p


def test_read_parquet_columns_matches_polars(tmp_path):
    p = _write_parquet(tmp_path)
    got = _read_parquet_columns(p)
    exp = pl.read_parquet(p).to_dict(as_series=False)
    assert got == exp
