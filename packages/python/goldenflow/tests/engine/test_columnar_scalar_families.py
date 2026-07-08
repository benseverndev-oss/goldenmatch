"""Phase 4d follow-on: the remaining clean scalar families on the columnar path.

Wires the last non-#1563, non-data-dependent transforms onto the Polars-free columnar
path via the ``scalar=`` mechanism:

- **phone** (`phone_e164`/`phone_national`/`phone_digits`/`phone_validate`/
  `phone_country_code`): the per-row `phonenumbers` reference is the SAME function the
  fast path settles residual rows with, and the fast path is parity-safe by
  construction (every tier agrees with the reference on rows it resolves), so the
  scalar output is byte-identical to the Polars engine. Only the columnar/Polars-free
  path uses the scalar; the default `transform_df` still runs the vectorized fast path.
- **email_validate** (str -> bool) and **name_script** (str -> str): owned kernels whose
  pure-Python reference is proven equal to the native kernel by the parity corpus.

Covers three dtypes: str (e164/national/digits/name_script), bool (validate), int
(country_code).
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
    (["phone_e164"], "c", ["(415) 555-2671", "4155552671", "+44 20 7946 0958", "bad", None]),
    (["phone_national"], "c", ["4155552671", "+14155552671", "nope", None]),
    (["phone_digits"], "c", ["(415) 555-2671", "abc123", "", None]),
    (["phone_validate"], "c", ["4155552671", "123", "bad", None]),
    (["phone_country_code"], "c", ["+442079460958", "4155552671", "x", None]),
    (["email_validate"], "c", ["a@b.com", "nope", "x@y.co.uk", None]),
    (["name_script"], "c", ["John", "Иван", "Ω", "", None]),
    # mixed chain: a fused kernel followed by a scalar
    (["strip", "phone_e164"], "c", ["  4155552671 ", None]),
    (["strip", "lowercase", "email_validate"], "c", ["  A@B.COM ", "  nope ", None]),
]


@pytest.mark.parametrize("ops,col,data", CASES)
def test_scalar_family_dict_equals_polars(ops, col, data) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    dd = {col: data, "k": list(range(len(data)))}
    cfg = _cfg([(col, ops)])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dd, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(dd), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), f"{c} diverged for {ops}"
    assert _mrows(res.manifest) == _mrows(ref.manifest)


@pytest.mark.parametrize("ops,col,data", CASES)
def test_scalar_family_columnar_engine_equals_polars(monkeypatch, ops, col, data) -> None:
    """The GOLDENFLOW_ENGINE=columnar frame path matches value AND dtype."""
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    dd = {col: data, "k": list(range(len(data)))}
    df = pl.DataFrame(dd)
    cfg = _cfg([(col, ops)])
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = goldenflow.transform_df(df, config=cfg)
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    got = goldenflow.transform_df(df, config=cfg)
    assert got.df.equals(ref.df), f"value/dtype diverged for {ops}"
    assert got.df[col].dtype == ref.df[col].dtype
    assert _mrows(got.manifest) == _mrows(ref.manifest)


def test_scalar_families_are_polars_free() -> None:
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
                TransformSpec(column="p", ops=["phone_e164"]),
                TransformSpec(column="cc", ops=["phone_country_code"]),
                TransformSpec(column="e", ops=["email_validate"]),
                TransformSpec(column="n", ops=["name_script"]),
            ])
            res = goldenflow.transform(
                {"p": ["4155552671", None], "cc": ["+442079460958", None],
                 "e": ["a@b.com", None], "n": ["John", None], "k": [1, 2]},
                config=cfg,
            )
            assert res.columns["p"] == ["+14155552671", None], res.columns["p"]
            assert res.columns["cc"] == [44, None], res.columns["cc"]
            assert res.columns["e"] == [True, None], res.columns["e"]
            assert res.columns["n"] == ["Latin", None], res.columns["n"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr
