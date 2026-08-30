"""`write_csv_polars_parity` must produce the bytes polars' `write_csv` does.

The csv output contract is pinned to polars (see `output/writer.write_output`).
The arrow writer only ever runs on a polars-FREE install, where there is nothing
to compare against at runtime -- so the comparison has to happen here, in a test
environment that has both.

Any divergence found by this test is a bug in `_csv_arrow`, not a tolerated
delta: a user who later installs the `polars` extra must not see their output
files change spelling.
"""
from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest
from goldenmatch.output._csv_arrow import write_csv_polars_parity

pl = pytest.importorskip("polars", reason="parity is measured AGAINST polars")


CASES: dict[str, dict] = {
    "plain": {"a": [1, 2, 3], "b": ["x", "y", "z"]},
    "nulls_and_empty": {"a": [1, None, 3], "b": ["x", None, ""]},
    "delimiter_in_value": {"a": ["he, said", "plain", "x"], "b": ["1", "2", "3"]},
    "quotes_in_value": {"a": ['she "said"', 'a"b', "c"], "b": ["1", "2", "3"]},
    "newline_in_value": {"a": ["li\nne", "b", "c"], "b": ["1", "2", "3"]},
    "carriage_return": {"a": ["li\r\nne", "b", "c"], "b": ["1", "2", "3"]},
    "floats": {"a": [1.5, 2.0, None, 0.1], "b": ["w", "x", "y", "z"]},
    "negative_and_big": {"a": [-1.25, 1e16, -0.0, 3.0], "b": ["w", "x", "y", "z"]},
    "bools": {"a": [True, False, None], "b": ["x", "y", "z"]},
    "ints_and_nulls": {"a": [1, None, -3], "b": [0, 5, None]},
    "unicode": {"a": ["ünïcøde", "日本語", "emoji"], "b": ["1", "2", "3"]},
    "cluster_output_shape": {
        "__cluster_id__": [1, 2, 3],
        "__row_id__": [10, 11, 12],
        "__cluster_size__": [2, 3, 2],
        "__oversized__": [False, True, False],
    },
    "header_needing_quotes": {'we,ird': [1, 2], 'with"quote': ["a", "b"]},
    "all_null_column": {"a": [None, None], "b": ["x", "y"]},
    "single_row": {"a": [1], "b": ["only"]},
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_arrow_csv_matches_polars_bytes(name, tmp_path):
    table = pa.table(CASES[name])

    arrow_path = tmp_path / "arrow.csv"
    write_csv_polars_parity(table, arrow_path)

    polars_path = tmp_path / "polars.csv"
    pl.from_arrow(table).write_csv(polars_path)

    assert arrow_path.read_bytes() == polars_path.read_bytes(), (
        f"{name}\n arrow ={arrow_path.read_bytes()!r}\n polars={polars_path.read_bytes()!r}"
    )


def test_temporal_matches_polars(tmp_path):
    """Dates/datetimes render ISO-8601 the way polars spells them."""
    table = pa.table(
        {
            "d": pa.array([dt.date(2026, 1, 2), dt.date(1999, 12, 31), None]),
            "s": ["a", "b", "c"],
        }
    )
    arrow_path = tmp_path / "a.csv"
    polars_path = tmp_path / "p.csv"
    write_csv_polars_parity(table, arrow_path)
    pl.from_arrow(table).write_csv(polars_path)
    assert arrow_path.read_bytes() == polars_path.read_bytes()


def test_null_and_empty_string_stay_distinguishable(tmp_path):
    """The distinction the hand-rolled writer exists for: a null is an empty
    field, an empty string is ``""``. Reading back must recover both."""
    table = pa.table({"a": [None, ""], "b": ["x", "y"]})
    path = tmp_path / "a.csv"
    write_csv_polars_parity(table, path)
    assert path.read_text(encoding="utf-8") == 'a,b\n,x\n"",y\n'
    back = pl.read_csv(path)
    assert back["a"].to_list() == [None, ""]
