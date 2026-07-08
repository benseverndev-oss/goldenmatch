"""Zero-gap Wave 3: numeric-INPUT array ops on the columnar path.

``round`` / ``clamp`` / ``abs_value`` / ``fill_zero`` take an already-numeric column, so
they don't fit the ``string* parser f64*`` numeric shape (there is no string->number
parser). A **synthetic ``AsFloat`` coerce** (native-flow ``numeric_columnar``) is
prepended to a PURE numeric-only chain — matching Polars ``cast(Float64, strict=False)``
and emitting no manifest record (the engine applies ``round(cast(f64))`` as one step) —
so the chain reuses the existing f64-op machinery + the ``float_to_polars_string``
formatter. A numeric-INPUT dict column is stringified via the native ``format_f64``
(exact Polars float format) so it round-trips and the manifest matches.

Byte-identical to the Polars engine over an 1800-case nan-aware randomized stress
(Float64 / Int64 / numeric-string inputs). **Documented edge:** a literal ``-0.0`` in a
*fused multi-op* numeric chain — the engine is internally inconsistent there (a single op
counts ``-0.0 -> 0.0`` as affected via ``cast(Utf8) !=``; a fused chain doesn't, via raw
f64 ``!=`` where ``-0.0 == 0.0``); values + samples always match, only the affected COUNT
can differ by the number of ``-0.0`` cells. The tests avoid ``-0.0`` for the manifest
assertion and check values+samples separately.
"""
from __future__ import annotations

import math

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
    return (
        nm is not None
        and columnar.native_columns_ready(nm)
        and hasattr(nm, "format_f64")  # numeric-input needs the AsFloat kernel + formatter
    )


def _veq(a, b) -> bool:
    """Value equality treating NaN == NaN (Python's `nan != nan` would false-fail)."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
            continue
        if x != y:
            return False
    return True


READY_CASES = [["round:1"], ["round:0"], ["clamp:-1:5"], ["abs_value"], ["fill_zero"],
               ["round:2", "abs_value"], ["abs_value", "clamp:0:10"]]


@pytest.mark.parametrize("ops", READY_CASES)
def test_numeric_input_is_columnar_ready(ops) -> None:
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-format_f64 wheel)")
    assert columnar.config_is_columnar_ready(_cfg([("c", ops)]))


def test_string_then_numeric_still_declines() -> None:
    """A numeric op AFTER a string op is ambiguous (string on a numeric column) — it
    stays on the Polars engine, not synthesized."""
    if not _native_ready():
        pytest.skip("native core not built")
    assert not columnar.config_is_columnar_ready(_cfg([("c", ["strip", "round:1"])]))


CASES = [
    (["round:1"], [1.55, 2.449, -3.2, None, 100.0]),
    (["clamp:-1:5"], [-5.0, 5.0, 15.0, None]),
    (["abs_value"], [-3.2, 4.0, None, 0.0]),
    (["fill_zero"], [1.0, None, 3.0]),
    (["round:2", "abs_value"], [-1.559, 2.0, None, 123456.789]),
    (["round:1"], [1, 2, -3, None, 100]),          # Int64 input -> Float64 out
    (["round:1"], ["1.55", "abc", "1e2", None]),   # numeric-string input
    (["abs_value"], ["-3.2", "", "nan", None]),
]


@pytest.mark.parametrize("ops,data", CASES)
def test_numeric_input_dict_equals_polars(ops, data) -> None:
    if not _native_ready():
        pytest.skip("native core not built")
    dd = {"c": data, "k": list(range(len(data)))}
    res = goldenflow.transform(dict(dd), config=_cfg([("c", ops)]))
    ref = goldenflow.transform_df(pl.DataFrame(dd), config=_cfg([("c", ops)]))
    assert _veq(res.columns["c"], ref.df["c"].to_list()), ops
    assert _mrows(res.manifest) == _mrows(ref.manifest), ops


@pytest.mark.parametrize("ops,data", CASES)
def test_numeric_input_columnar_engine_equals_polars(monkeypatch, ops, data) -> None:
    if not _native_ready():
        pytest.skip("native core not built")
    df = pl.DataFrame({"c": data, "k": list(range(len(data)))})
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = goldenflow.transform_df(df, config=_cfg([("c", ops)]))
    monkeypatch.setenv("GOLDENFLOW_ENGINE", "columnar")
    got = goldenflow.transform_df(df, config=_cfg([("c", ops)]))
    assert got.df["c"].dtype == ref.df["c"].dtype  # Float64 out
    assert _veq(got.df["c"].to_list(), ref.df["c"].to_list()), ops
    assert _mrows(got.manifest) == _mrows(ref.manifest), ops


def test_numeric_input_is_polars_free() -> None:
    import subprocess
    import sys
    import textwrap

    if not _native_ready():
        pytest.skip("native core not built")
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            """
            import sys, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            cfg = GoldenFlowConfig(transforms=[TransformSpec(column="c", ops=["round:1", "abs_value"])])
            res = goldenflow.transform({"c": [-1.55, 2.449, None], "k": [1, 2, 3]}, config=cfg)
            assert res.columns["c"] == [1.6, 2.4, None], res.columns["c"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr
