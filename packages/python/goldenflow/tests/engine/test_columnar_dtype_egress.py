"""Phase 4d dtype-egress: int/bool-returning scalars on the columnar path.

The scalar mechanism egressed only ``str`` results. This extension adds a
``scalar_dtype`` tag ("str"/"int"/"bool"/"float") so a scalar transform can egress a
correctly-typed column and format manifest samples/affected counts like Polars'
``cast(Utf8)`` (bool -> "true"/"false", int -> str(int)). Unlocks:
- categorical ``boolean_normalize`` (bool),
- dates ``date_validate`` (bool) + ``extract_year``/``extract_month``/``extract_day``/
  ``extract_quarter`` (int),
all running Polars-free, byte-identical (frame value+dtype AND manifest) to the Polars
engine.
"""
from __future__ import annotations

import goldenflow
import polars as pl
import pytest
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _cfg(specs):
    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


def _mrows(m):
    return [
        (r.column, r.transform, r.affected_rows, tuple(r.sample_before or []),
         tuple(r.sample_after or [])) for r in m.records
    ]


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


CASES = [
    (["boolean_normalize"], "b", ["yes", "no", "TRUE", "f", "1", "maybe", None]),
    (["date_validate"], "d", ["2020-03-15", "not a date", "", None, "1995"]),
    (["extract_year"], "d", ["2020-03-15", "March 1995", None, "bad"]),
    (["extract_month"], "d", ["2020-03-15", None, "bad"]),
    (["extract_day"], "d", ["2020-03-15", "March 5, 2021", None]),
    (["extract_quarter"], "d", ["2020-08-15", "2020-01-01", None]),
    # mixed: an owned string op then a dtype-changing scalar
    (["strip", "boolean_normalize"], "b", ["  yes ", "NO", None]),
    (["strip", "extract_year"], "d", ["  2020-03-15 ", "1995", None]),
    # all-null / all-unparseable -> the typed-null egress edge
    (["boolean_normalize"], "b", ["x", "y", None]),
    (["extract_year"], "d", ["bad", None]),
]


@pytest.mark.parametrize("ops,col,data", CASES)
def test_dtype_scalar_dict_equals_polars(ops, col, data) -> None:
    """transform(dict) (Polars-free) == transform_df, values + manifest."""
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    dd = {col: data, "k": list(range(len(data)))}
    cfg = _cfg([(col, ops)])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dd, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(dd), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), f"{c} diverged"
    assert _mrows(res.manifest) == _mrows(ref.manifest)


@pytest.mark.parametrize("ops,col,data", CASES)
def test_dtype_scalar_columnar_engine_equals_polars(monkeypatch, ops, col, data) -> None:
    """The GOLDENFLOW_ENGINE=columnar frame path egresses the correct dtype
    (Boolean/Int64), matching the Polars engine including .dtype and the all-null edge."""
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    dd = {col: data, "k": list(range(len(data)))}
    df = pl.DataFrame(dd)
    cfg = _cfg([(col, ops)])
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = goldenflow.transform_df(df, config=cfg)
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    got = goldenflow.transform_df(df, config=cfg)
    assert got.df.equals(ref.df)
    assert got.df[col].dtype == ref.df[col].dtype
    assert _mrows(got.manifest) == _mrows(ref.manifest)


def test_dtype_scalar_is_polars_free() -> None:
    import subprocess
    import sys
    import textwrap

    if not _native_ready():
        pytest.skip("native in-memory core not built")
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            cfg = GoldenFlowConfig(transforms=[
                TransformSpec(column="b", ops=["strip", "boolean_normalize"]),
                TransformSpec(column="d", ops=["extract_year"]),
            ])
            res = goldenflow.transform(
                {"b": ["  yes ", "no", None], "d": ["2020-03-15", "1995", None], "k": [1, 2, 3]},
                config=cfg,
            )
            assert res.columns["b"] == [True, False, None], res.columns["b"]
            assert res.columns["d"] == [2020, 1995, None], res.columns["d"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
