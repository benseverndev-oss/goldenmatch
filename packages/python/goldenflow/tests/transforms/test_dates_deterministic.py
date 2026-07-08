"""Phase 4d: dates are now DETERMINISTIC (owned source of truth) + columnar.

The date family used to fill missing fields via dateutil's default = ``today``, so
``date_iso8601("March 1995")`` returned a different day on every run -- a latent
non-determinism bug, and inconsistent with the year-string fast path (which fills
month/day with 1). We pinned the fill to Jan 1 (`_DEFAULT_DATE`), which:
1. makes partial dates deterministic,
2. makes the residual agree with the fast path, and
3. makes the date scalars byte-reproducible -> the str-returning date transforms run
   on the Polars-free columnar path (via the wave-1 `scalar=` mechanism).
"""
from __future__ import annotations

import goldenflow
import polars as pl
import pytest
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar
from goldenflow.transforms.dates import (
    _date_iso8601_py,
    date_iso8601,
)


def test_partial_dates_are_deterministic() -> None:
    """A missing month/day fills to 1, not today -- stable across calls, and matching
    the year-string fast path."""
    assert _date_iso8601_py("March 1995") == "1995-03-01"
    assert _date_iso8601_py("March 1995") == _date_iso8601_py("March 1995")
    # year-only agrees with the vectorized fast path (both -> -01-01)
    assert _date_iso8601_py("1995") == "1995-01-01"
    assert date_iso8601(pl.Series(["1995"])).to_list()[0] == "1995-01-01"
    # fully-specified dates are unaffected
    assert _date_iso8601_py("2020-03-15") == "2020-03-15"


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


def _cfg(specs):
    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


def _mrows(m):
    return [
        (r.column, r.transform, r.affected_rows, tuple(r.sample_before or []),
         tuple(r.sample_after or [])) for r in m.records
    ]


# str-returning date transforms + a mixed chain. Inputs where the vectorized fast path
# and the deterministic scalar agree (full dates, year-only, invalid passthrough,
# fast-returns-null partials) -- the common cases. (An ambiguous "Month Year" partial
# like "March 1995" hits a SEPARATE pre-existing fast-path looseness where the columnar
# scalar is actually more correct; that's out of scope here.)
DATE_CASES = [
    (["date_iso8601"], ["2020-03-15", "March 5, 2021", "1995", "invalid", None, "2023/01/05"]),
    (["date_us"], ["2020-03-15", "1995", None, "bad"]),
    (["date_eu"], ["2020-03-15", "1995", None, "bad"]),
    (["date_parse"], ["2020-03-15", "March 5, 2021", "1995", None]),
    (["datetime_iso8601"], ["2020-03-15", "March 5, 2021", None, "invalid"]),
    (["extract_day_of_week"], ["2020-03-15", "2021-01-01", None, "bad"]),
    (["strip", "date_iso8601"], ["  2020-03-15 ", "1995", None]),
]


@pytest.mark.parametrize("ops,data", DATE_CASES)
def test_dates_columnar_equals_polars(ops, data) -> None:
    """transform(dict) (Polars-free) == transform_df(pl.DataFrame), data + manifest."""
    if not _native_ready():
        pytest.skip("native in-memory core not built (pre-0.25 wheel)")
    dd = {"d": data, "k": list(range(len(data)))}
    cfg = _cfg([("d", ops)])
    assert columnar.config_is_columnar_ready(cfg)
    res = goldenflow.transform(dd, config=cfg)
    ref = goldenflow.transform_df(pl.DataFrame(dd), config=cfg)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), f"{c} diverged"
    assert _mrows(res.manifest) == _mrows(ref.manifest)


def test_dates_columnar_is_polars_free() -> None:
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
            cfg = GoldenFlowConfig(transforms=[TransformSpec(column="d", ops=["strip", "date_iso8601"])])
            res = goldenflow.transform({"d": ["  2020-03-15 ", "1995", None], "k": [1, 2, 3]}, config=cfg)
            assert res.columns["d"] == ["2020-03-15", "1995-01-01", None], res.columns["d"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout
