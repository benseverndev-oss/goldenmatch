"""Phase 4c: the Polars-free public entry point ``goldenflow.transform``.

``transform(dict[str, list], config)`` returns a ``ColumnarResult`` (``.columns`` +
``.manifest``). A config the native columnar engine covers runs with Polars NEVER
imported; an uncovered config declines to the Polars engine (byte-identical). The
Polars-typed ``transform_df`` is unchanged for existing ``pl.DataFrame`` callers.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import goldenflow
import polars as pl
import pytest
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _cfg(specs):
    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


@pytest.mark.parametrize(
    "specs,data",
    [
        ([("name", ["strip", "lowercase"])], {"name": ["  Hi ", "BYE", None], "k": [1, 2, 3]}),
        (
            [("price", ["currency_strip", "round:1"])],
            {"price": ["$1,234.56", "$0.5", None], "k": [1, 2, 3]},
        ),
        ([("name", ["split_name"])], {"name": ["John Smith", "Cher", None], "k": [1, 2, 3]}),
    ],
)
def test_transform_dict_equals_transform_df(specs, data) -> None:
    """transform(dict) == transform_df(pl.DataFrame) for covered configs."""
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    cfg = _cfg(specs)
    res = goldenflow.transform(data, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(data), config=cfg)
    assert list(res.columns.keys()) == ref.df.columns
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list()

    def rows(m):
        return [
            (r.column, r.transform, r.affected_rows, tuple(r.sample_before or []),
             tuple(r.sample_after or [])) for r in m.records
        ]

    assert rows(res.manifest) == rows(ref.manifest)


def test_transform_uncovered_config_matches_polars() -> None:
    """An uncovered config (phone_e164) declines to the Polars engine, byte-identical."""
    cfg = _cfg([("p", ["strip", "phone_e164"])])
    data = {"p": ["  212-555-0100 ", "bad"], "k": [1, 2]}
    res = goldenflow.transform(data, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(data), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list()


def test_transform_rejects_dataframe_input() -> None:
    """A pl.DataFrame belongs to transform_df, not transform(dict)."""
    with pytest.raises(TypeError, match="transform_df"):
        goldenflow.transform(pl.DataFrame({"x": [1]}), config=_cfg([("x", ["strip"])]))


def test_to_polars_bridge() -> None:
    res = goldenflow.transform({"x": ["  a "], "k": [1]}, config=_cfg([("x", ["strip"])]))
    df = res.to_polars()
    assert df.shape == (1, 2)
    assert df["x"].to_list() == ["a"]


def test_transform_covered_is_polars_free() -> None:
    """Subprocess: a covered transform() call imports no Polars."""
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            cfg = GoldenFlowConfig(transforms=[TransformSpec(column="x", ops=["strip", "lowercase"])])
            res = goldenflow.transform({"x": ["  Hi ", "BYE"], "k": [1, 2]}, config=cfg)
            assert res.columns["x"] == ["hi", "bye"], res.columns
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
