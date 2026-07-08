"""Phase 4d: scalar-chain columnar coverage (the transform-signature port).

A transform that registers a pure-Python ``scalar`` (``str|None -> value``) can run on
the in-memory columnar engine WITHOUT Polars -- applied op-by-op over a list, composing
with owned Rust-kernel ops in the same chain. The pilot family is address
(state_abbreviate / state_expand / address_standardize / address_expand /
zip_normalize / country_standardize / unit_normalize): all str->str with byte-parity
between the native kernel (used by the Polars engine) and the pure-Python scalar (used
by the columnar path), so the two are byte-identical.

The CSV path stays Rust-only (it can't call a Python scalar), so a scalar spec is
in-memory-columnar-ready but NOT file-ready.
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


ADDRESS_CASES = [
    ([("st", ["state_abbreviate"])], {"st": ["California", "TX", "New York", None, ""]}),
    ([("st", ["state_expand"])], {"st": ["CA", "tx", None, "ZZ"]}),
    ([("z", ["zip_normalize"])], {"z": ["12345-6789", "  90210 ", None, "bad"]}),
    ([("a", ["address_standardize"])], {"a": ["123 Main Street", None, "5 Oak Ave"]}),
    ([("a", ["address_expand"])], {"a": ["123 Main St", None]}),
    ([("c", ["country_standardize"])], {"c": ["USA", "united states", None]}),
    ([("u", ["unit_normalize"])], {"u": ["Apt 5", "#3", None]}),
    # mixed: owned string kernels compose with a scalar op in one chain
    ([("st", ["strip", "lowercase", "state_expand"])], {"st": ["  CA  ", "TX", None]}),
    ([("a", ["strip", "address_standardize"])], {"a": ["  123 Main Street  ", None]}),
]


@pytest.mark.parametrize("specs,data", ADDRESS_CASES)
def test_scalar_chain_transform_dict_equals_polars(specs, data) -> None:
    """transform(dict) (Polars-free) == transform_df(pl.DataFrame) for address scalar
    chains, data + manifest."""
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    data = {**data, "keep": list(range(len(next(iter(data.values())))))}
    cfg = _cfg(specs)
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(data, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(data), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), f"{c} diverged"
    assert _mrows(res.manifest) == _mrows(ref.manifest)


@pytest.mark.parametrize("specs,data", ADDRESS_CASES)
def test_scalar_chain_columnar_engine_equals_polars(monkeypatch, specs, data) -> None:
    """The GOLDENFLOW_ENGINE=columnar transform_df path also handles scalar chains."""
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    data = {**data, "keep": list(range(len(next(iter(data.values())))))}
    df = pl.DataFrame(data)
    cfg = _cfg(specs)
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = goldenflow.transform_df(df, config=cfg)
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    got = goldenflow.transform_df(df, config=cfg)
    assert got.df.equals(ref.df)
    assert _mrows(got.manifest) == _mrows(ref.manifest)


def test_scalar_spec_inmemory_ready_but_not_file_ready() -> None:
    """A scalar spec runs on the in-memory columnar path but declines on the CSV path
    (the Rust CSV kernel can't call a Python scalar)."""
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    cfg = _cfg([("st", ["state_expand"])])
    assert columnar.config_is_columnar_ready(cfg) is True
    assert columnar.columnar_file_ready(cfg) is False


def test_scalar_chain_is_polars_free() -> None:
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
                TransformSpec(column="st", ops=["strip", "state_expand"]),
                TransformSpec(column="a", ops=["address_standardize"]),
            ])
            res = goldenflow.transform(
                {"st": ["  ca ", "TX", None], "a": ["123 Main Street", None, "5 Oak Ave"], "k": [1, 2, 3]},
                config=cfg,
            )
            assert res.columns["st"] == ["California", "Texas", None], res.columns["st"]
            assert res.columns["a"] == ["123 Main St", None, "5 Oak Ave"], res.columns["a"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
