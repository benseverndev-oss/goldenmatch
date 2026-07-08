"""Finish the eviction: Polars-free DB read + CSV zero-config.

- **DB read** — :func:`goldenflow.connectors.database.read_database_columns` reads any
  PEP-249 (DBAPI) connection into a typed ``dict[str, list]`` with no polars/connectorx,
  feeding straight into :func:`goldenflow.transform`.
- **CSV zero-config** — ``transform("<x>.csv", config=None)`` now runs Polars-free: the
  columns are profiled AS TEXT (their on-disk form) and auto-cleaned. This is the OWNED
  behavior — numeric-looking IDs/zips stay strings (``"01234"`` is a zip, not ``1234``),
  the data-cleaning-correct choice; the cleaned text values match
  ``transform_df(pl.read_csv(...))`` even though Polars would coerce numeric columns.
"""
from __future__ import annotations

import csv
import sqlite3
import sys

import goldenflow
import pytest
from goldenflow.connectors.database import read_database_columns
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def test_read_database_columns_typed() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE c (name TEXT, amount INTEGER, price REAL)")
    conn.executemany(
        "INSERT INTO c VALUES (?,?,?)",
        [("  John ", 1, 1.5), ("jane", 2, 2.5), (None, 3, None)],
    )
    cols = read_database_columns(conn, "SELECT * FROM c")
    assert cols == {"name": ["  John ", "jane", None], "amount": [1, 2, 3],
                    "price": [1.5, 2.5, None]}


def test_transform_from_database_is_polars_free() -> None:
    """DB read + transform in a fresh interpreter — polars never imported."""
    import subprocess
    import textwrap

    if not _native_ready():
        pytest.skip("native core not built")
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys, sqlite3, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            from goldenflow.connectors.database import read_database_columns
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE TABLE c (name TEXT)")
            conn.executemany("INSERT INTO c VALUES (?)", [("  HI ",), ("Jane",)])
            cols = read_database_columns(conn, "SELECT * FROM c")
            cfg = GoldenFlowConfig(transforms=[TransformSpec(column="name", ops=["strip", "lowercase"])])
            res = goldenflow.transform(cols, config=cfg)
            assert res.columns["name"] == ["hi", "jane"], res.columns["name"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr


def test_csv_zero_config_runs_polars_free(tmp_path) -> None:
    if not _native_ready():
        pytest.skip("native core not built")
    p = tmp_path / "in.csv"
    _write_csv(p, [["name", "zip", "amount"], ["  John SMITH ", "01234", "5"],
                   ["jane", "90210", "10"], [None, None, "15"]])
    res = goldenflow.transform(str(p), config=None)
    # name auto-cleaned (strip etc.); zip/amount profiled AS TEXT (owned: no int coercion)
    assert res.columns["name"][0] == "John SMITH"
    assert res.columns["zip"] == ["01234", "90210", None]   # leading zero preserved
    assert res.columns["amount"] == ["5", "10", "15"]        # stays string, not [5,10,15]


def test_csv_zero_config_cleaned_text_matches_polars(tmp_path) -> None:
    """The CLEANED TEXT values match transform_df(pl.read_csv(...), config=None) even
    though Polars coerces numeric columns (the owned behavior differs only in dtype +
    the manifest of untransformed numeric columns)."""
    import polars as pl

    p = tmp_path / "in.csv"
    _write_csv(p, [["name", "city"], ["  John SMITH ", "  NYC "], ["jane", "nyc"], [None, "LA"]])
    res = goldenflow.transform(str(p), config=None)
    ref = goldenflow.transform_df(pl.read_csv(p), config=None)
    for c in ref.df.columns:
        as_text_ref = [str(x) if x is not None else None for x in ref.df[c].to_list()]
        assert res.columns[c] == as_text_ref, c
