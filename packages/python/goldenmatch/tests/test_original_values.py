"""#3003: input records come back with the values as entered.

`unique` / `dupes` / `deduplicated` held post-standardization values:
"14 Oak Street" came back "14 Oak St", "María González" as "Maria Gonzalez",
"555-201-3344" as "5552013344". A user asking for their records minus the
duplicates got different records. Golden records stay standardized -- they are
synthesized, not input.
"""

import csv

import goldenmatch as gm
import pyarrow as pa
import pytest
from goldenmatch.core import output_tables
from typer.testing import CliRunner

ROWS = [
    ("1", "Maria Gonzalez", "maria.gonzalez@example.com", "555-201-3344", "14 Oak Street", "62704"),
    ("2", "Maria Gonzales", "mgonzalez@example.com", "(555) 201-3344", "14 Oak St", "62704"),
    ("3", "María González", "maria.gonzalez@example.com", "5552013344", "14 Oak St.", "62704"),
    ("4", "James O'Neil", "jim.oneil@example.com", "555-883-1200", "902 Pine Avenue", "84065"),
    ("5", "Jim O'Neill", "jim.oneil@example.com", "555.883.1200", "902 Pine Ave", "84065"),
    ("6", "Priya Raman", "priya.raman@example.com", "555-410-7788", "3 Birch Road", "49116"),
    ("7", "Priya Ramen", "p.raman@example.com", "555-410-7788", "3 Birch Rd", "49116"),
    ("8", "Thomas Becker", "tbecker@example.com", "555-667-0192", "77 Elm Court", "97024"),
    ("9", "Aisha Bello", "aisha.bello@example.com", "555-339-2471", "510 Cedar Lane", "29601"),
    ("10", "Chen Wei", "chen.wei@example.com", "555-722-9031", "8 Maple Drive", "95361"),
    ("11", "Wei Chen", "chen.wei@example.com", "555-722-9031", "8 Maple Dr", "95361"),
    ("12", "Thomas Baker", "thomas.baker@example.com", "555-120-4455", "41 Walnut Way", "97024"),
]
HEADER = ["id", "name", "email", "phone", "address", "zip"]
AS_ENTERED_UNIQUE = {"77 Elm Court", "510 Cedar Lane", "41 Walnut Way"}


@pytest.fixture
def customers(tmp_path):
    path = tmp_path / "customers.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(ROWS)
    return path


def test_file_dedupe_returns_values_as_entered(customers):
    r = gm.dedupe(str(customers))
    assert set(r.unique.column("address").to_pylist()) == AS_ENTERED_UNIQUE
    names = r.dupes.column("name").to_pylist()
    phones = r.dupes.column("phone").to_pylist()
    assert "María González" in names and "(555) 201-3344" in phones
    # The deduplicated list's unique rows are as entered too.
    assert AS_ENTERED_UNIQUE <= set(r.deduplicated.column("address").to_pylist())


def test_keep_original_values_false_returns_standardized(customers):
    r = gm.dedupe(str(customers), keep_original_values=False)
    assert "77 Elm Court" not in r.unique.column("address").to_pylist()


def test_dedupe_df_returns_values_as_entered():
    table = pa.table({h: [row[i] for row in ROWS] for i, h in enumerate(HEADER)})
    r = gm.dedupe_df(table)
    assert set(r.unique.column("address").to_pylist()) == AS_ENTERED_UNIQUE


def test_file_input_above_the_threshold_is_standardized_unless_forced(customers, monkeypatch):
    monkeypatch.setattr(output_tables, "ORIGINAL_VALUES_MAX_ROWS", 5)
    assert "77 Elm Court" not in gm.dedupe(str(customers)).unique.column("address").to_pylist()
    forced = gm.dedupe(str(customers), keep_original_values=True)
    assert set(forced.unique.column("address").to_pylist()) == AS_ENTERED_UNIQUE


def test_in_memory_input_ignores_the_threshold(monkeypatch):
    monkeypatch.setattr(output_tables, "ORIGINAL_VALUES_MAX_ROWS", 5)
    table = pa.table({h: [row[i] for row in ROWS] for i, h in enumerate(HEADER)})
    assert set(gm.dedupe_df(table).unique.column("address").to_pylist()) == AS_ENTERED_UNIQUE


def test_cli_unique_file_as_entered_and_flag_overrides(customers, tmp_path):
    from goldenmatch.cli.main import app

    def unique_addresses(*extra):
        out = tmp_path / ("o" + "".join(extra).replace("-", ""))
        res = CliRunner().invoke(
            app,
            ["dedupe", str(customers), "--output-unique", "--output-dir", str(out),
             "--run-name", "r", "-q", *extra],
        )
        assert res.exit_code == 0, res.output
        with (out / "r_unique.csv").open(encoding="utf-8") as f:
            return {row["address"] for row in csv.DictReader(f)}

    assert unique_addresses() == AS_ENTERED_UNIQUE
    assert "77 Elm Court" not in unique_addresses("--standardized-values")


def test_restore_matches_rows_by_row_id_not_position():
    originals = pa.table({"name": ["Ann", "Bo", "Cy"], "__row_id__": [10, 11, 12]})
    table = pa.table({"name": ["CY", "ANN"], "__row_id__": [12, 10], "__cluster_id__": [3, 1]})
    out = output_tables.restore_original_values(table, originals)
    assert out.column("name").to_pylist() == ["Cy", "Ann"]
    assert out.column("__cluster_id__").to_pylist() == [3, 1]
