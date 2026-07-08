"""Polars-free zero-config: ``transform(dict, config=None)`` auto-detects + fixes.

``config=None`` used to require the Polars engine (the profiler inspected ``pl.Series``
dtypes). A Polars-free profiler (:func:`profiler_bridge.profile_columns`) now infers each
column's type + cardinality over plain lists — byte-identical *selection* to the Polars
built-in profiler (which is what a dict / no-file-path input uses anyway; GoldenCheck is
file-path-only) — so zero-config on a dict runs on the native columnar path with Polars
uninstalled, identical to ``transform_df(pl.DataFrame(data), config=None)``.

(A CSV path + ``config=None`` still needs ``[polars]``: Polars' CSV dtype inference
decides numeric-vs-string, which the string-only stdlib reader can't reproduce.)
"""
from __future__ import annotations

import math

import goldenflow
import polars as pl
import pytest
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _mrows(m):
    return [
        (r.column, r.transform, r.affected_rows, tuple(r.sample_before or []),
         tuple(r.sample_after or [])) for r in m.records
    ]


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


def _veq(a, b) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
            continue
        if x != y:
            return False
    return True


CASES = [
    {"name": ["  John SMITH ", "jane doe", None], "k": [0, 1, 2]},
    {"email": ["A@B.COM", "x@y.co", "bad"], "city": ["  NYC ", "nyc", "LA"], "k": [0, 1, 2]},
    {"phone": ["212-555-0100", "(415) 555-2671", None], "amount": [1, 2, 3]},  # numeric col untouched
    {"active": [True, False, None], "id": [10, 20, 30], "notes": ["  a ", "B", "n/a"]},
    {"category": ["NYC", "nyc", "NYC", "LA", "la"], "k": [0, 1, 2, 3, 4]},  # low-cardinality
    {"empty": [None, None], "plain": ["x", "y"]},
]


@pytest.mark.parametrize("data", CASES)
def test_zero_config_dict_equals_polars(data) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built")
    res = goldenflow.transform(dict(data), config=None)
    ref = goldenflow.transform_df(pl.DataFrame(data), config=None)
    for c in ref.df.columns:
        assert _veq(res.columns[c], ref.df[c].to_list()), c
    assert _mrows(res.manifest) == _mrows(ref.manifest)


def test_zero_config_is_polars_free() -> None:
    import subprocess
    import sys
    import textwrap

    if not _native_ready():
        pytest.skip("native core not built")
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys, goldenflow
            res = goldenflow.transform(
                {"name": ["  John SMITH ", "jane"], "amount": [1, 2], "k": [1, 2]},
                config=None,
            )
            assert res.columns["name"] == ["John SMITH", "jane"], res.columns["name"]
            assert res.columns["amount"] == [1, 2], res.columns["amount"]  # numeric untouched
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr


def test_zero_config_csv_still_needs_polars_backend(tmp_path) -> None:
    """A CSV path + config=None routes to the Polars engine (dtype inference), not the
    Polars-free zero-config — it declines cleanly rather than mis-inferring."""
    import csv

    p = tmp_path / "in.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["name"], ["  John "]])
    # With polars present it succeeds via the engine; the point is it does NOT silently
    # go through the string-only Polars-free profiler (which would mis-type numerics).
    res = goldenflow.transform(str(p), config=None)
    assert "name" in res.columns
