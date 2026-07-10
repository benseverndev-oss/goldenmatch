"""Corpus parity test: ``goldenmatch.core.io_arrow.read_table_arrow`` vs the
polars ``goldenmatch.core.ingest.load_file(...).collect()`` reference.

This is gate #1 of the Polars-eviction parity contract (see
``goldenmatch/core/io_arrow.py`` module docstring): every reader divergence
gets fixed AT THE READER, not downstream. Each case here is read by BOTH
engines and compared on:

  - column names (order-sensitive)
  - row count
  - per-cell values, each engine cast to its own string type (nulls as None)
  - a coarse "neutral dtype class" (str/int/float/bool/date/datetime/other)

Known reader deltas (documented, not test-suppressed): NONE as of this
writing. The one real divergence found during development -- pyarrow's CSV
reader infers ``date32``/``timestamp`` for ISO-date-shaped strings while
polars' ``scan_csv``/``read_csv`` never auto-parses dates (no
``try_parse_dates`` is ever passed by ``load_file``) -- was fixed at the
reader: ``io_arrow`` probes the inferred schema and forces any temporal
column back to string via ``ConvertOptions(column_types=...)`` before the
real read, so the raw source text passes through unparsed on both engines.

Error-parity case (``junk_row``): ``load_file``'s default CSV path passes no
``ignore_errors``/``truncate_ragged_lines`` knob, so a wrong-column-count row
makes polars raise. Parity means arrow raises too -- NOT that both silently
tolerate the junk row.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pytest
from goldenmatch.core.ingest import load_file
from goldenmatch.core.io_arrow import read_table_arrow

# --------------------------------------------------------------------------
# dtype canonicalization helpers
# --------------------------------------------------------------------------


def _neutral_dtype_polars(dtype: pl.DataType) -> str:
    if dtype == pl.Utf8:
        return "str"
    if dtype == pl.Boolean:
        return "bool"
    if dtype in (
        pl.Int8, pl.Int16, pl.Int32, pl.Int64,
        pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
    ):
        return "int"
    if dtype in (pl.Float32, pl.Float64):
        return "float"
    if dtype == pl.Date:
        return "date"
    if isinstance(dtype, pl.Datetime):
        return "datetime"
    return "other"


def _neutral_dtype_arrow(dtype: pa.DataType) -> str:
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return "str"
    if pa.types.is_boolean(dtype):
        return "bool"
    if pa.types.is_integer(dtype):
        return "int"
    if pa.types.is_floating(dtype):
        return "float"
    if pa.types.is_date(dtype):
        return "date"
    if pa.types.is_timestamp(dtype):
        return "datetime"
    return "other"


def _assert_parity(arrow_table: pa.Table, polars_df: pl.DataFrame) -> None:
    assert list(arrow_table.column_names) == list(polars_df.columns), (
        f"column names differ: arrow={arrow_table.column_names} "
        f"polars={polars_df.columns}"
    )
    assert arrow_table.num_rows == polars_df.height, (
        f"row count differs: arrow={arrow_table.num_rows} polars={polars_df.height}"
    )

    for name in polars_df.columns:
        pl_dtype = polars_df.schema[name]
        arrow_dtype = arrow_table.schema.field(name).type
        pl_class = _neutral_dtype_polars(pl_dtype)
        arrow_class = _neutral_dtype_arrow(arrow_dtype)
        assert pl_class == arrow_class, (
            f"dtype class mismatch on column {name!r}: "
            f"polars={pl_dtype!r} ({pl_class}) vs arrow={arrow_dtype!r} ({arrow_class})"
        )

        pl_values = polars_df[name].cast(pl.Utf8).to_list()
        arrow_values = pc.cast(arrow_table.column(name), pa.string()).to_pylist()
        assert arrow_values == pl_values, (
            f"value mismatch on column {name!r}: arrow={arrow_values!r} "
            f"polars={pl_values!r}"
        )


# --------------------------------------------------------------------------
# corpus case builders -- each returns (path, load_file_kwargs)
# --------------------------------------------------------------------------


def _case_sample_csv(tmp_path: Path) -> tuple[Path, dict]:
    """Mirrors conftest.py's ``sample_csv`` fixture data inline."""
    path = tmp_path / "sample.csv"
    df = pl.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "first_name": ["John", "john", "Jane", "JOHN", "Bob"],
        "last_name": ["Smith", "Smith", "Doe", "Smyth", "Jones"],
        "email": [
            "john@example.com", "john@example.com", "jane@test.com",
            "john.s@example.com", "bob@test.com",
        ],
        "zip": ["19382", "19382", "10001", "19383", "90210"],
        "phone": [
            "267-555-1234", "267-555-1234", "212-555-9999",
            "267-555-1235", "310-555-0000",
        ],
    })
    df.write_csv(path)
    return path, {}


def _case_sample_csv_b(tmp_path: Path) -> tuple[Path, dict]:
    """Mirrors conftest.py's ``sample_csv_b`` fixture data inline."""
    path = tmp_path / "sample_b.csv"
    df = pl.DataFrame({
        "id": [101, 102, 103],
        "first_name": ["John", "Alice", "Jane"],
        "last_name": ["Smith", "Wonder", "Doe"],
        "email": ["jsmith@work.com", "alice@test.com", "jane@test.com"],
        "zip": ["19382", "30301", "10001"],
        "phone": ["267-555-1234", "404-555-1111", "212-555-9999"],
    })
    df.write_csv(path)
    return path, {}


def _case_latin1_accented(tmp_path: Path) -> tuple[Path, dict]:
    """(a) latin-1 encoded CSV with accented names, AUTO encoding detection."""
    path = tmp_path / "latin1.csv"
    text = (
        "id,name,city\n"
        "1,Jos\xe9 Mu\xf1oz,Bogot\xe1\n"
        "2,Andr\xe9 L\xe9vy,Montr\xe9al\n"
        "3,Fran\xe7oise \xc9tienne,Qu\xe9bec\n"
    )
    path.write_bytes(text.encode("latin-1"))
    return path, {}


def _case_latin1_explicit_encoding(tmp_path: Path) -> tuple[Path, dict]:
    """Same dirty bytes, but with an EXPLICIT non-utf8/non-lossy codec name."""
    path = tmp_path / "latin1_explicit.csv"
    text = "id,name\n1,Jos\xe9 Mu\xf1oz\n2,Caf\xe9\n"
    path.write_bytes(text.encode("latin-1"))
    return path, {"encoding": "latin-1"}


def _case_invalid_utf8_bytes(tmp_path: Path) -> tuple[Path, dict]:
    """(b) UTF-8 CSV with invalid byte sequences, written directly as bytes."""
    path = tmp_path / "invalid_utf8.csv"
    raw = (
        b"id,name,note\n"
        b"1,Foo,clean\n"
        b"2,Bar\x80Baz,lone continuation byte\n"
        b"3,Qux\xffQuux,invalid start byte\n"
    )
    path.write_bytes(raw)
    return path, {}


def _case_leading_zero_zip(tmp_path: Path) -> tuple[Path, dict]:
    """(d) CSV with leading-zero zips -- both engines infer int and strip it."""
    path = tmp_path / "leading_zero_zip.csv"
    path.write_text("id,zip\n1,01234\n2,90210\n3,00501\n", encoding="utf-8")
    return path, {}


def _case_iso_dates(tmp_path: Path) -> tuple[Path, dict]:
    """(e) CSV with ISO date strings -- must stay string on both engines."""
    path = tmp_path / "iso_dates.csv"
    path.write_text(
        "id,signup_date\n1,2024-01-15\n2,2024-02-20\n3,2023-12-31\n",
        encoding="utf-8",
    )
    return path, {}


def _case_explicit_utf8_lossy(tmp_path: Path) -> tuple[Path, dict]:
    """Explicit ``encoding='utf8-lossy'`` on an otherwise-clean file."""
    path = tmp_path / "explicit_lossy.csv"
    path.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")
    return path, {"encoding": "utf8-lossy"}


def _case_explicit_utf8_strict(tmp_path: Path) -> tuple[Path, dict]:
    """Explicit ``encoding='utf8'`` (strict) on a clean file."""
    path = tmp_path / "explicit_strict.csv"
    path.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")
    return path, {"encoding": "utf8"}


def _case_custom_separator(tmp_path: Path) -> tuple[Path, dict]:
    """Pipe-delimited file exercising the ``separator=`` passthrough."""
    path = tmp_path / "piped.psv"  # not .csv -- pass delimiter explicitly
    path.write_text("id|name|zip\n1|Alice|19382\n2|Bob|10001\n", encoding="utf-8")
    return path, {"delimiter": "|"}


_VALUE_PARITY_CASES = [
    ("sample_csv", _case_sample_csv),
    ("sample_csv_b", _case_sample_csv_b),
    ("latin1_accented_auto", _case_latin1_accented),
    ("latin1_explicit_encoding", _case_latin1_explicit_encoding),
    ("invalid_utf8_bytes_auto", _case_invalid_utf8_bytes),
    ("leading_zero_zip", _case_leading_zero_zip),
    ("iso_dates", _case_iso_dates),
    ("explicit_utf8_lossy", _case_explicit_utf8_lossy),
    ("explicit_utf8_strict", _case_explicit_utf8_strict),
    ("custom_separator", _case_custom_separator),
]


@pytest.mark.parametrize(
    "case_builder", [c[1] for c in _VALUE_PARITY_CASES], ids=[c[0] for c in _VALUE_PARITY_CASES]
)
def test_csv_parity(tmp_path: Path, case_builder) -> None:
    path, kwargs = case_builder(tmp_path)

    delimiter = kwargs.pop("delimiter", ",")
    encoding = kwargs.pop("encoding", None)
    assert not kwargs, f"unconsumed kwargs: {kwargs}"

    polars_df = load_file(path, delimiter=delimiter, encoding=encoding).collect()
    arrow_table = read_table_arrow(path, separator=delimiter, encoding=encoding)

    _assert_parity(arrow_table, polars_df)


def test_csv_junk_row_error_parity(tmp_path: Path) -> None:
    """(c) CSV with a wrong-column-count row -- both engines must ERROR.

    ``load_file``'s default CSV path (parse_mode='auto', no explicit
    ignore_errors/truncate_ragged_lines) surfaces polars' ComputeError on a
    ragged row. Parity means arrow raises too, not that arrow silently
    tolerates what polars rejects.
    """
    path = tmp_path / "junk_row.csv"
    path.write_text(
        "id,name,zip\n1,John,19382\n2,Jane\n3,Bob,90210,extra\n", encoding="utf-8"
    )

    with pytest.raises(Exception):
        load_file(path).collect()

    with pytest.raises(Exception):
        read_table_arrow(path)


# --------------------------------------------------------------------------
# Parquet + Excel (Task 3)
# --------------------------------------------------------------------------


def _case_parquet(tmp_path: Path) -> tuple[Path, dict]:
    """Parquet case -- mirrors conftest.py's ``sample_parquet`` fixture."""
    path = tmp_path / "sample.parquet"
    df = pl.DataFrame({
        "id": [1, 2, 3],
        "first_name": ["John", "Jane", "Bob"],
        "last_name": ["Smith", "Doe", "Jones"],
        "email": ["john@example.com", "jane@test.com", "bob@test.com"],
        "zip": ["19382", "10001", "90210"],
    })
    df.write_parquet(path)
    return path, {}


def test_parquet_parity(tmp_path: Path) -> None:
    path, kwargs = _case_parquet(tmp_path)
    polars_df = load_file(path, **kwargs).collect()
    arrow_table = read_table_arrow(path)
    _assert_parity(arrow_table, polars_df)


def _build_sample_xlsx(tmp_path: Path):
    """Two-sheet workbook: Sheet1 (active/default) has a numeric + text
    column; Sheet2 exercises ``sheet=`` selection."""
    import openpyxl

    path = tmp_path / "sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["id", "name", "amount"])
    ws.append([1, "Alice", 19382])
    ws.append([2, "Bob", 10001])
    ws.append([3, "Carol", 90210])

    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["id", "note"])
    ws2.append([10, "from sheet two"])
    ws2.append([20, "another row"])

    wb.save(path)
    return path


def test_excel_parity_default_sheet(tmp_path: Path) -> None:
    path = _build_sample_xlsx(tmp_path)
    polars_df = load_file(path).collect()
    arrow_table = read_table_arrow(path)
    _assert_parity(arrow_table, polars_df)


def test_excel_parity_named_sheet(tmp_path: Path) -> None:
    path = _build_sample_xlsx(tmp_path)
    polars_df = load_file(path, sheet="Sheet2").collect()
    arrow_table = read_table_arrow(path, sheet="Sheet2")
    _assert_parity(arrow_table, polars_df)
