"""Phase 4d wave 6: parameterized scalars on the columnar path.

Some transforms' per-element reference depends on the op's params (``date_shift:7``,
``age_from_dob:2024-01-01``). A ``scalar_factory`` (params -> fn(val)) on the registry
lets the columnar engine build the scalar from the spec's params, so these run
Polars-free byte-identical to the Polars engine. This is the last non-excluded scalar
shape (after str, int, and bool returns).
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
    (["date_shift:7"], "d", ["2020-03-15", "1995", None, "bad"]),
    (["date_shift:-30"], "d", ["2020-03-15", None]),
    (["date_shift"], "d", ["2020-03-15", None]),  # no param -> days=0
    (["age_from_dob:2024-01-01"], "d", ["2000-03-15", "1990-06-20", None, "bad"]),
    (["strip", "date_shift:1"], "d", ["  2020-03-15 ", None]),
    (["strip", "age_from_dob:2024-06-01"], "d", ["  2000-01-01 ", None]),
]


@pytest.mark.parametrize("ops,col,data", CASES)
def test_parameterized_scalar_dict_equals_polars(ops, col, data) -> None:
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
def test_parameterized_scalar_columnar_engine_equals_polars(monkeypatch, ops, col, data) -> None:
    """The GOLDENFLOW_ENGINE=columnar frame path matches too (value + dtype)."""
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


def test_parameterized_scalar_is_polars_free() -> None:
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
                TransformSpec(column="d", ops=["date_shift:7"]),
                TransformSpec(column="b", ops=["age_from_dob:2024-01-01"]),
            ])
            res = goldenflow.transform(
                {"d": ["2020-03-15", None], "b": ["2000-03-15", None], "k": [1, 2]},
                config=cfg,
            )
            assert res.columns["d"] == ["2020-03-22", None], res.columns["d"]
            assert res.columns["b"] == [23, None], res.columns["b"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
