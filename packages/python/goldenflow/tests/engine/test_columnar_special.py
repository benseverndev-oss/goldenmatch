"""Zero-gap Wave 2: the two special-shape transforms on the columnar path.

- ``merge_name`` — multi-INPUT (reads the source column + ``last_name``, appends
  ``full_name``). mode=dataframe; identity on the source column.
- ``initial_expand`` — flag-only (value identity + flagged rows recorded as manifest
  ERRORS via the ``has_initial`` predicate).

Both run Polars-free over plain lists (references ``_merge_name_py`` / ``_has_initial_py``
proven equal to the goldenflow-core kernels) and are byte-identical to the Polars engine:
values, appended columns, manifest records AND errors.
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


def _errs(m):
    return [(e.column, e.transform, e.row, e.error) for e in m.errors]


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


MERGE = {"first_name": ["John", "Jane", None, "Al"], "last_name": ["Smith", None, "Doe", "Roe"],
         "k": [0, 1, 2, 3]}
INIT = {"name": ["John A. Smith", "Jane Doe", "R. J. Brown", None], "k": [0, 1, 2, 3]}
AC = {"c": ["Apple", "apple", "APPLE", "aple", "Banana", "banana", "bananna", "Cherry",
            "cherry", "cherri", "Apple", "Apple", None], "k": list(range(13))}


def test_category_auto_correct_dict_equals_polars() -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    cfg = _cfg([("c", ["category_auto_correct"])])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dict(AC), config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(AC), config=cfg)
    assert res.columns["c"] == ref.df["c"].to_list()
    assert _mrows(res.manifest) == _mrows(ref.manifest)


def test_merge_name_dict_equals_polars() -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    cfg = _cfg([("first_name", ["merge_name"])])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dict(MERGE), config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(MERGE), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), c
    assert "full_name" in res.columns
    assert _mrows(res.manifest) == _mrows(ref.manifest)


def test_merge_name_no_last_name_is_noop() -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    data = {"first_name": ["John", "Jane"], "k": [0, 1]}
    cfg = _cfg([("first_name", ["merge_name"])])
    res = goldenflow.transform(dict(data), config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(data), config=cfg)
    assert ("full_name" in res.columns) == ("full_name" in ref.df.columns)
    assert _mrows(res.manifest) == _mrows(ref.manifest)


def test_initial_expand_dict_equals_polars() -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    cfg = _cfg([("name", ["initial_expand"])])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dict(INIT), config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(INIT), config=cfg)
    assert res.columns["name"] == ref.df["name"].to_list()
    assert _mrows(res.manifest) == _mrows(ref.manifest)
    assert _errs(res.manifest) == _errs(ref.manifest)  # flagged rows match


@pytest.mark.parametrize("data,col,op", [
    (MERGE, "first_name", "merge_name"),
    (INIT, "name", "initial_expand"),
    (AC, "c", "category_auto_correct"),
])
def test_special_columnar_engine_equals_polars(monkeypatch, data, col, op) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    df = pl.DataFrame(data)
    cfg = _cfg([(col, [op])])
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = goldenflow.transform_df(df, config=cfg)
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    got = goldenflow.transform_df(df, config=cfg)
    assert got.df.equals(ref.df)
    assert _mrows(got.manifest) == _mrows(ref.manifest)
    assert _errs(got.manifest) == _errs(ref.manifest)


def test_special_are_polars_free() -> None:
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
                TransformSpec(column="first_name", ops=["merge_name"]),
                TransformSpec(column="nm", ops=["initial_expand"]),
            ])
            res = goldenflow.transform(
                {"first_name": ["John", None], "last_name": ["Smith", "Doe"],
                 "nm": ["A. B. Cee", "plain"], "k": [1, 2]},
                config=cfg,
            )
            assert res.columns["full_name"] == ["John Smith", "Doe"], res.columns["full_name"]
            assert [e.row for e in res.manifest.errors] == [0], res.manifest.errors
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr
