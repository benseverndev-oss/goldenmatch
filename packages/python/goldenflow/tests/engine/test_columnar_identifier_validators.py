"""Phase 4d: the 12 checksummed-identifier ``*_validate`` transforms on the columnar path.

The last clean identifier batch (the formatters landed in #1563). Each validator has a
pure-Python reference (``_X_validate_py``) proven equal to the goldenflow-core Rust
kernel by the byte-parity corpus; registering it as ``scalar=`` (``scalar_dtype="bool"``)
makes it run on the Polars-free columnar path byte-identically to the Polars engine.
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
    (["cc_validate"], ["4111111111111111", "1234", None]),
    (["iban_validate"], ["GB82WEST12345698765432", "bad", None]),
    (["isbn_validate"], ["9780306406157", "0306406152", "x", None]),
    (["ean_validate"], ["4006381333931", "123", None]),
    (["swift_validate"], ["DEUTDEFF", "nope", None]),
    (["vat_validate"], ["DE123456789", "x", None]),
    (["aba_validate"], ["021000021", "111", None]),
    (["imei_validate"], ["490154203237518", "123", None]),
    (["isin_validate"], ["US0378331005", "bad", None]),
    (["cusip_validate"], ["037833100", "x", None]),
    (["npi_validate"], ["1234567893", "111", None]),
    (["luhn_validate"], ["4111111111111111", "1235", None]),
    # mixed: a fused string kernel then the bool validator
    (["strip", "cc_validate"], ["  4111111111111111 ", None]),
]


@pytest.mark.parametrize("ops,data", CASES)
def test_validator_dict_equals_polars(ops, data) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    dd = {"c": data, "k": list(range(len(data)))}
    cfg = _cfg([("c", ops)])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dd, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(dd), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), f"{c} diverged for {ops}"
    assert _mrows(res.manifest) == _mrows(ref.manifest)


@pytest.mark.parametrize("ops,data", CASES)
def test_validator_columnar_engine_equals_polars(monkeypatch, ops, data) -> None:
    """The GOLDENFLOW_ENGINE=columnar frame path matches value AND Boolean dtype."""
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    dd = {"c": data, "k": list(range(len(data)))}
    df = pl.DataFrame(dd)
    cfg = _cfg([("c", ops)])
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = goldenflow.transform_df(df, config=cfg)
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    got = goldenflow.transform_df(df, config=cfg)
    assert got.df.equals(ref.df), f"value/dtype diverged for {ops}"
    assert got.df["c"].dtype == ref.df["c"].dtype == pl.Boolean
    assert _mrows(got.manifest) == _mrows(ref.manifest)


def test_validators_are_polars_free() -> None:
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
                TransformSpec(column="cc", ops=["cc_validate"]),
                TransformSpec(column="ib", ops=["iban_validate"]),
                TransformSpec(column="np", ops=["npi_validate"]),
            ])
            res = goldenflow.transform(
                {"cc": ["4111111111111111", None], "ib": ["GB82WEST12345698765432", None],
                 "np": ["1234567893", None], "k": [1, 2]},
                config=cfg,
            )
            assert res.columns["cc"] == [True, None], res.columns["cc"]
            assert res.columns["ib"] == [True, None], res.columns["ib"]
            assert res.columns["np"] == [True, None], res.columns["np"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr
