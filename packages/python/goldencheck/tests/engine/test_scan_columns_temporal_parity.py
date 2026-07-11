"""S2.3 byte-identity gate: TemporalOrderProfiler produces identical Findings on the
native-date-backed PyFrame vs PolarsFrame, and scan_columns includes them polars-free."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck import scan_columns
from goldencheck.core._native_loader import native_enabled
from goldencheck.core.frame import PolarsFrame, PyFrame
from goldencheck.relations.temporal import TemporalOrderProfiler

pytestmark = pytest.mark.skipif(not native_enabled("str_to_date"), reason="needs native date kernel")


def _datasets():
    return [
        {"start_date": ["2021-05-01", "2021-01-01", "2021-03-01"],
         "end_date":   ["2021-01-01", "2021-06-01", "2021-02-01"]},
        {"created": ["2020-01-01", "2020-02-01"], "updated": ["2020-06-01", "2020-07-01"]},
        {"name": ["a", "b"], "qty": ["1", "2"]},
        {"signup": ["2021-01-01", None, "2021-09-01"], "last_login": ["2020-01-01", "2021-01-01", "2021-10-01"]},
    ]


@pytest.mark.parametrize("data", _datasets())
def test_temporal_backend_parity(data):
    pol = TemporalOrderProfiler().profile(PolarsFrame(pl.DataFrame(data)))
    pyf = TemporalOrderProfiler().profile(PyFrame.from_columns(data))
    assert pyf == pol


@pytest.mark.parametrize("data", _datasets())
def test_scan_columns_includes_temporal(data):
    from goldencheck.engine.scanner import _HARD_PROFILERS, _MECHANICAL_PROFILERS
    pol = PolarsFrame(pl.DataFrame(data))
    expected = []
    for name in data:
        for profiler in (*_MECHANICAL_PROFILERS, *_HARD_PROFILERS):
            expected.extend(profiler.profile(pol, name))
    expected.extend(TemporalOrderProfiler().profile(pol))
    assert scan_columns(data) == expected
