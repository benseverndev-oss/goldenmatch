"""S2.1 byte-identity gate: the covered profilers produce identical Findings on the
pure-Python PyFrame backend and the Polars PolarsFrame backend (run with polars
present). This proves scan_columns(dict) == the Polars covered-check output, so the
polars-absent nopolars-lane literals are trustworthy."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck import scan_columns
from goldencheck.core.frame import PolarsFrame, PyFrame
from goldencheck.profilers.cardinality import CardinalityProfiler
from goldencheck.profilers.nullability import NullabilityProfiler
from goldencheck.profilers.uniqueness import UniquenessProfiler


# Data exercising each covered finding branch. NOTE: floats are NaN-FREE on purpose --
# PyColumn.sort/n_unique assume no NaN (Polars sorts NaN last; Python does not). Do NOT
# add NaN to any column here.
def _datasets():
    return [
        {"pk": list(range(120)),                                   # 100% unique -> PK finding
         "grade": ["A", "B", "C"] * 40,                            # low cardinality enum
         "note": [None] * 120,                                     # entirely null
         "score": [float(i % 7) for i in range(120)]},             # clean floats, low card
        {"user_id": [1, 1, 2, 3] * 30,                             # identifier w/ dups (near-unique? no)
         "email": [f"u{i}" for i in range(120)],                   # 100% unique non-id
         "opt": ([1] * 114) + [None] * 6},                         # ~5% nulls in sizeable col
        # Exercise the WARNING branches the spec calls out:
        {"account_id": list(range(119)) + [0],                     # 119 unique of 120, name has 'id' + 1 dup -> uniqueness WARNING
         "phone": ([f"p{i}" for i in range(118)]) + [None, None]}, # 118/120 non-null (>95%), total>=100 -> nullability WARNING
        {"x": [1, 2, 3]},                                          # tiny frame (<10, <50) -> few/no findings
    ]


@pytest.mark.parametrize("data", _datasets())
def test_covered_profilers_backend_parity(data):
    pol = PolarsFrame(pl.DataFrame(data))
    pyf = PyFrame.from_columns(data)
    for profiler in (NullabilityProfiler(), UniquenessProfiler(), CardinalityProfiler()):
        for col in data:
            assert profiler.profile(pol, col) == profiler.profile(pyf, col), (profiler, col)


@pytest.mark.parametrize("data", _datasets())
def test_scan_columns_matches_polars_covered_output(data):
    from goldencheck.core._native_loader import native_enabled
    from goldencheck.engine.scanner import _HARD_PROFILERS, _MECHANICAL_PROFILERS
    pol = PolarsFrame(pl.DataFrame(data))
    covered = list(_MECHANICAL_PROFILERS)
    if native_enabled("regex"):
        covered += _HARD_PROFILERS
    expected = []
    for name in data:
        for profiler in covered:
            expected.extend(profiler.profile(pol, name))
    from goldencheck.relations.temporal import TemporalOrderProfiler
    if native_enabled("str_to_date"):
        expected.extend(TemporalOrderProfiler().profile(pol))
    assert scan_columns(data) == expected
