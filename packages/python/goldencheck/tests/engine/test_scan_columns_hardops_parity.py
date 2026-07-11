"""S2.2 byte-identity gate: encoding/format/pattern profilers produce identical Findings
on the native-backed PyFrame vs PolarsFrame (polars present). Proves scan_columns' hard-op
coverage == the Polars path, so the nopolars-lane assertions are trustworthy."""
from __future__ import annotations

import polars as pl
import pytest
from goldencheck import scan_columns
from goldencheck.core._native_loader import native_enabled
from goldencheck.core.frame import PolarsFrame, PyFrame
from goldencheck.profilers.encoding_detection import EncodingDetectionProfiler
from goldencheck.profilers.format_detection import FormatDetectionProfiler
from goldencheck.profilers.pattern_consistency import PatternConsistencyProfiler

pytestmark = pytest.mark.skipif(not native_enabled("regex"), reason="needs native regex kernel")


def _datasets():
    return [
        {"email": [f"u{i}@x.com" for i in range(18)] + ["notanemail", "also bad"]},
        {"note": ["cafe", "café", "naïve", "plain", "x​zero"]},
        {"code": ["AB-12", "CD-34", "EF-56", "X"]},
        {"nums": [1, 2, 3]},
        {"phone": ["(555) 123-4567"] * 15 + ["555.111.2222"] * 3 + ["bad", None]},
    ]


@pytest.mark.parametrize("data", _datasets())
def test_hard_profilers_backend_parity(data):
    pol = PolarsFrame(pl.DataFrame(data))
    pyf = PyFrame.from_columns(data)
    for profiler in (EncodingDetectionProfiler(), FormatDetectionProfiler(), PatternConsistencyProfiler()):
        for col in data:
            assert profiler.profile(pol, col) == profiler.profile(pyf, col), (type(profiler).__name__, col)


@pytest.mark.parametrize("data", _datasets())
def test_scan_columns_includes_hard_checks(data):
    from goldencheck.engine.scanner import _HARD_PROFILERS, _MECHANICAL_PROFILERS
    pol = PolarsFrame(pl.DataFrame(data))
    expected = []
    for name in data:
        for profiler in (*_MECHANICAL_PROFILERS, *_HARD_PROFILERS):
            expected.extend(profiler.profile(pol, name))
    from goldencheck.relations.temporal import TemporalOrderProfiler
    if native_enabled("str_to_date"):
        expected.extend(TemporalOrderProfiler().profile(pol))
    assert scan_columns(data) == expected
