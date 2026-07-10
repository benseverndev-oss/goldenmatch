"""Phase 4e: ``transform()`` accepts a CSV path Polars-free.

The public ``transform()`` already runs a covered config over a ``dict[str, list]``
with Polars never imported. This closes the read side: hand it a ``.csv`` path and the
stdlib-``csv`` reader (:func:`goldenflow.engine.columnar.read_csv_columns`) ingests it
into the same ``dict`` shape — cell-identical to ``pl.read_csv(infer_schema_length=0)``
(every field a string; empty -> ``None``). So the full read -> transform -> result flow
works with ``goldenflow[polars]`` uninstalled.
"""
from __future__ import annotations

import csv

import goldenflow
import polars as pl
import pytest
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
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


def test_read_csv_columns_matches_polars(tmp_path) -> None:
    p = tmp_path / "in.csv"
    _write_csv(p, [
        ["name", "note", "empty"],
        ["  John SMITH ", 'has,comma', ""],
        ["jane", 'line\nbreak', "x"],
        ["", "trailing", ""],
    ])
    got = columnar.read_csv_columns(p)
    ref = pl.read_csv(p, infer_schema_length=0)
    assert list(got.keys()) == ref.columns
    for c in ref.columns:
        assert got[c] == ref[c].to_list(), f"{c} diverged"


def test_read_csv_columns_empty_file(tmp_path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    assert columnar.read_csv_columns(p) == {}


def test_transform_csv_path_equals_dict_and_polars(tmp_path) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    p = tmp_path / "in.csv"
    _write_csv(p, [
        ["name", "price", "dob"],
        ["  John SMITH ", "$1,234.50", "2000-03-15"],
        ["jane", "$0.5", "1990"],
        ["", "", "bad"],
    ])
    cfg = GoldenFlowConfig(transforms=[
        TransformSpec(column="name", ops=["strip", "lowercase"]),
        TransformSpec(column="price", ops=["currency_strip", "round:1"]),
        TransformSpec(column="dob", ops=["date_iso8601"]),
    ])
    assert columnar.config_is_columnar_ready(cfg)
    from_path = goldenflow.transform(p, config=cfg)
    from_dict = goldenflow.transform(columnar.read_csv_columns(p), config=cfg)
    ref = goldenflow.transform_df(pl.read_csv(p, infer_schema_length=0), config=cfg)
    for c in ref.df.columns:
        assert from_path.columns[c] == ref.df[c].to_list(), f"{c} diverged"
        assert from_path.columns[c] == from_dict.columns[c]


def test_transform_non_csv_path_rejected(tmp_path) -> None:
    p = tmp_path / "in.parquet"
    p.write_bytes(b"")
    cfg = GoldenFlowConfig(transforms=[TransformSpec(column="a", ops=["strip"])])
    with pytest.raises(ValueError, match="csv"):
        goldenflow.transform(p, config=cfg)


def test_transform_csv_path_is_polars_free(tmp_path) -> None:
    import subprocess
    import sys
    import textwrap

    if not _native_ready():
        pytest.skip("native in-memory core not built")
    p = tmp_path / "in.csv"
    _write_csv(p, [["name", "price"], ["  Hi ", "$1234.50"], ["JANE", "$0.5"]])
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            f"""
            import sys, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            cfg = GoldenFlowConfig(transforms=[
                TransformSpec(column="name", ops=["strip", "lowercase"]),
                TransformSpec(column="price", ops=["currency_strip"]),
            ])
            res = goldenflow.transform(r"{p}", config=cfg)
            assert res.columns["name"] == ["hi", "jane"], res.columns["name"]
            assert res.columns["price"] == [1234.5, 0.5], res.columns["price"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr
