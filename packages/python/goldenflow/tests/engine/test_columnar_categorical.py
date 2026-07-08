"""Phase 4d wave 3: categorical str->str transforms on the columnar path.

`gender_standardize` and `null_standardize` are owned, deterministic str->str
kernels -- their pure-Python scalar is byte-parity-equal to the native kernel the
Polars engine uses, so they run on the in-memory columnar path Polars-free via the
`scalar=` mechanism. (`boolean_normalize` returns a bool, so it awaits the
dtype-aware scalar egress wave -- not included here.)
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
    (["gender_standardize"], "g", ["Male", "f", "FEMALE", None, "X", ""]),
    (["null_standardize"], "v", ["n/a", "value", "NULL", "", None, "-"]),
    (["strip", "null_standardize"], "v", ["  n/a  ", "  value ", None]),
    (["strip", "lowercase", "gender_standardize"], "g", ["  MALE ", "F", None]),
]


@pytest.mark.parametrize("ops,col,data", CASES)
def test_categorical_columnar_equals_polars(ops, col, data) -> None:
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


def test_categorical_columnar_is_polars_free() -> None:
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
                TransformSpec(column="g", ops=["strip", "lowercase", "gender_standardize"]),
                TransformSpec(column="v", ops=["null_standardize"]),
            ])
            res = goldenflow.transform(
                {"g": ["  MALE ", "f", None], "v": ["n/a", "value", "NULL"], "k": [1, 2, 3]},
                config=cfg,
            )
            assert res.columns["g"] == ["M", "F", None], res.columns["g"]
            assert res.columns["v"] == [None, "value", None], res.columns["v"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
