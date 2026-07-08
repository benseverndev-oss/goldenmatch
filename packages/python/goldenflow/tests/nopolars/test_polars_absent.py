"""Phase 4f prototype: goldenflow works with **polars genuinely uninstalled**.

This is the living proof for the Polars-eviction end state. Every other Polars-free
test in the suite still ``import polars`` to compare a covered result against the
``transform_df`` reference, so none of them can run in a polars-absent interpreter.
This module imports polars NOWHERE — it asserts covered outputs against hardcoded
literals (the byte-parity corpus already proves those literals equal the Polars
engine) — so it is the one module that can run under a base install with ``polars``
moved to the ``[polars]`` extra.

It is ``skipif``'d OUT of the normal suite (where polars IS present), so it is inert
there and only executes in the dedicated ``goldenflow_nopolars`` CI lane (and any
local run where polars is absent). When 4f flips ``polars`` out of the base deps for
the 2.0 major, this lane is already green — it de-risks the breaking bump.
"""
from __future__ import annotations

import importlib.util

import pytest

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    _HAS_POLARS,
    reason="polars-absent proof — only runs where polars is NOT installed (the 4f lane)",
)


def test_import_goldenflow_without_polars() -> None:
    import sys

    import goldenflow  # must not raise, must not import polars

    assert "polars" not in sys.modules
    # sanity: the public covered entry points exist
    assert hasattr(goldenflow, "transform")
    assert hasattr(goldenflow, "transform_df")  # exists; needs [polars] to run


def test_native_core_is_ready_without_polars() -> None:
    from goldenflow.core._native_loader import native_module
    from goldenflow.engine import columnar

    nm = native_module()
    if nm is None or not columnar.native_columns_ready(nm):
        pytest.skip("native in-memory core not built (the covered path needs it)")


def _cfg(specs):
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


def _need_native():
    from goldenflow.core._native_loader import native_module
    from goldenflow.engine import columnar

    nm = native_module()
    if nm is None or not columnar.native_columns_ready(nm):
        pytest.skip("native in-memory core not built")


def test_covered_transform_dict_without_polars() -> None:
    import sys

    import goldenflow

    _need_native()
    cfg = _cfg([
        ("name", ["strip", "lowercase"]),
        ("price", ["currency_strip", "round:1"]),
        ("dob", ["date_iso8601"]),
        ("flag", ["boolean_normalize"]),
    ])
    res = goldenflow.transform(
        {
            "name": ["  John SMITH ", "jane", None],
            "price": ["$1,234.50", "$0.5", ""],
            "dob": ["2000-03-15", "1990", "bad"],
            "flag": ["yes", "no", "maybe"],
            "k": [1, 2, 3],
        },
        config=cfg,
    )
    assert res.columns["name"] == ["john smith", "jane", None]
    assert res.columns["price"] == [1234.5, 0.5, None]
    assert res.columns["dob"] == ["2000-03-15", "1990-01-01", "bad"]
    assert res.columns["flag"] == [True, False, None]
    assert "polars" not in sys.modules


def test_covered_transform_csv_path_without_polars(tmp_path) -> None:
    import csv
    import sys

    import goldenflow

    _need_native()
    p = tmp_path / "in.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "price"])
        w.writerow(["  Hi ", "$1234.50"])
        w.writerow(["JANE", "$0.5"])
    cfg = _cfg([("name", ["strip", "lowercase"]), ("price", ["currency_strip"])])
    res = goldenflow.transform(p, config=cfg)
    assert res.columns["name"] == ["hi", "jane"]
    assert res.columns["price"] == [1234.5, 0.5]
    assert "polars" not in sys.modules


def test_uncovered_path_raises_clean_error_without_polars() -> None:
    """Touching the lazy Polars proxy with polars absent surfaces a plain
    ``ModuleNotFoundError`` (the uncovered tail is loudly, optionally Polars)."""
    from goldenflow._polars_lazy import pl

    with pytest.raises(ModuleNotFoundError):
        _ = pl.DataFrame  # attribute access triggers the deferred import
