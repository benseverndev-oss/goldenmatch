"""Phase 4d wave 5: identifier FORMATTERS on the columnar path.

The 10 owned str->str identifier formatters (ssn_format/ssn_mask/ein_format/
cc_format/cc_mask/cc_brand/iban_format/isbn_normalize/swift_format/vat_format) run
on the in-memory columnar path Polars-free via the `scalar=` mechanism -- their
pure-Python scalar is byte-parity-equal to the native kernel the Polars engine uses.
(The *_validate identifiers return bool and await the dtype-egress wiring.)
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
    (["ssn_format"], "s", ["123456789", "123-45-6789", "bad", None]),
    (["ssn_mask"], "s", ["123-45-6789", None]),
    (["ein_format"], "e", ["123456789", None]),
    (["cc_format"], "c", ["4111111111111111", None]),
    (["cc_mask"], "c", ["4111111111111111", None]),
    (["cc_brand"], "c", ["4111111111111111", "5500000000000004", None]),
    (["iban_format"], "i", ["GB82WEST12345698765432", None]),
    (["isbn_normalize"], "b", ["9780306406157", "0306406152", None]),
    (["swift_format"], "w", ["DEUTDEFF", None]),
    (["vat_format"], "v", ["DE123456789", None]),
    (["strip", "ssn_format"], "s", ["  123456789 ", None]),
]


@pytest.mark.parametrize("ops,col,data", CASES)
def test_identifier_formatters_columnar_equals_polars(ops, col, data) -> None:
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


def test_identifier_formatters_polars_free() -> None:
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
            cfg = GoldenFlowConfig(transforms=[TransformSpec(column="s", ops=["strip", "ssn_format"])])
            res = goldenflow.transform({"s": ["  123456789 ", None], "k": [1, 2]}, config=cfg)
            assert res.columns["s"] == ["123-45-6789", None], res.columns["s"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
