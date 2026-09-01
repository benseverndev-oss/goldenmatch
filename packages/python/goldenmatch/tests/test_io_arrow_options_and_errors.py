"""Options and error paths of `core.io_arrow`, the polars-free ingest.

The happy path (`read_table_arrow` on a plain CSV) is covered by
`test_io_arrow_ingest_parity.py`. This file covers what that one does not: the
formats, the encoding modes, the two REPLACE-not-append rules, and the errors.

Every case here is a trap the module's own comments describe, which is a decent
signal it is worth a test -- two of them were live bugs while porting the CLI:
a duplicated `__source__` field, and `pa.concat_tables` refusing a multi-file
fixture whose column sets differed.
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from goldenmatch.core.io_arrow import read_files_arrow, read_table_arrow


def _csv(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# -- formats ----------------------------------------------------------------


def test_unsupported_suffix_names_the_format(tmp_path):
    bad = tmp_path / "notes.rtf"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file format"):
        read_table_arrow(bad)


def test_parquet_round_trips(tmp_path):
    src = pa.table({"id": [1, 2], "name": ["Ann", "Bo"]})
    path = tmp_path / "in.parquet"
    pq.write_table(src, path)
    assert read_table_arrow(path).to_pydict() == src.to_pydict()


def test_xlsx_reads_the_active_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "name"])
    ws.append([1, "Ann"])
    path = tmp_path / "book.xlsx"
    wb.save(path)

    out = read_table_arrow(path)
    assert out.column_names == ["id", "name"]
    assert out.to_pydict()["name"] == ["Ann"]


def test_xlsx_can_select_a_named_sheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["id"])
    wb.active.append([1])
    second = wb.create_sheet("people")
    second.append(["id", "name"])
    second.append([7, "Cy"])
    path = tmp_path / "multi.xlsx"
    wb.save(path)

    out = read_table_arrow(path, sheet="people")
    assert out.column_names == ["id", "name"]
    assert out.to_pydict()["id"] == [7]


# -- encoding ---------------------------------------------------------------


def test_strict_utf8_rejects_invalid_bytes_and_lossy_accepts_them(tmp_path):
    """`encoding="utf8"` matches polars' strict mode: invalid bytes raise
    rather than being silently replaced. `utf8-lossy` is the replacing one."""
    path = tmp_path / "latin.csv"
    path.write_bytes("name\nCaf\xe9\n".encode("latin-1"))

    with pytest.raises(ValueError, match="invalid utf-8 sequence"):
        read_table_arrow(path, encoding="utf8")

    lossy = read_table_arrow(path, encoding="utf8-lossy")
    assert lossy.num_rows == 1


def test_a_named_encoding_is_decoded_with_replacement(tmp_path):
    path = tmp_path / "cp1252.csv"
    path.write_bytes("name\nCaf\xe9\n".encode("latin-1"))
    out = read_table_arrow(path, encoding="latin-1")
    assert out.to_pydict()["name"] == ["Caf\xe9"]


# -- read_files_arrow -------------------------------------------------------


def test_no_files_is_an_error_not_an_empty_table(tmp_path):
    with pytest.raises(ValueError, match="at least one file"):
        read_files_arrow([])


def test_differing_column_sets_union_rather_than_raising(tmp_path):
    """`promote_options="permissive"` is what `pl.concat(how="diagonal")` did.
    Plain `pa.concat_tables` raises here -- the trap that blew up a multi-file
    auto-config fixture."""
    a = _csv(tmp_path, "a.csv", "id,name\n1,Ann\n")
    b = _csv(tmp_path, "b.csv", "id,email\n2,b@x.com\n")

    out = read_files_arrow([a, b])
    assert set(out.column_names) == {"id", "name", "email"}
    cols = out.to_pydict()
    assert cols["name"] == ["Ann", None]
    assert cols["email"] == [None, "b@x.com"]


def test_source_column_labels_from_the_stem_or_the_spec(tmp_path):
    a = _csv(tmp_path, "alpha.csv", "id\n1\n")
    b = _csv(tmp_path, "beta.csv", "id\n2\n")

    out = read_files_arrow([a, (b, "renamed")], source_column="__source__")
    assert out.to_pydict()["__source__"] == ["alpha", "renamed"]


def test_source_column_replaces_an_existing_one(tmp_path):
    """REPLACE, not append. `append_column` produces a duplicate field and the
    table then raises `Field "__source__" exists 2 times in schema` on the
    first lookup -- a real failure while porting the CLI."""
    a = _csv(tmp_path, "a.csv", "id,__source__\n1,preexisting\n")

    out = read_files_arrow([a], source_column="__source__")
    assert out.column_names.count("__source__") == 1
    assert out.to_pydict()["__source__"] == ["a"]


def test_row_id_column_is_added_and_is_global_across_files(tmp_path):
    a = _csv(tmp_path, "a.csv", "id\n1\n2\n")
    b = _csv(tmp_path, "b.csv", "id\n3\n")

    out = read_files_arrow([a, b], row_id_column="__row_id__")
    assert out.to_pydict()["__row_id__"] == [0, 1, 2]


def test_row_id_column_replaces_an_existing_one(tmp_path):
    a = _csv(tmp_path, "a.csv", "id,__row_id__\n1,99\n2,98\n")

    out = read_files_arrow([a], row_id_column="__row_id__")
    assert out.column_names.count("__row_id__") == 1
    assert out.to_pydict()["__row_id__"] == [0, 1]


def test_a_single_file_skips_the_concat_path(tmp_path):
    a = _csv(tmp_path, "solo.csv", "id,name\n1,Ann\n")
    out = read_files_arrow([a])
    assert out.to_pydict() == {"id": [1], "name": ["Ann"]}
