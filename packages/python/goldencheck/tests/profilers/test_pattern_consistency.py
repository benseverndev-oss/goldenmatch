import polars as pl
from goldencheck.models.finding import Severity
from goldencheck.profilers.pattern_consistency import (
    PatternConsistencyProfiler,
    _generalize,
    _generalize_series,
)


def test_mixed_patterns_flagged():
    # 10% minority → INFO (5-30% range = valid variant, not WARNING)
    df = pl.DataFrame({"phone": ["(555) 123-4567"] * 90 + ["555.123.4567"] * 10})
    findings = PatternConsistencyProfiler().profile(df, "phone")
    assert any(f.check == "pattern_consistency" for f in findings)


def test_rare_pattern_flagged_as_warning():
    # <5% minority → WARNING (very rare = likely error)
    df = pl.DataFrame({"phone": ["(555) 123-4567"] * 97 + ["555.123.4567"] * 3})
    findings = PatternConsistencyProfiler().profile(df, "phone")
    assert any(f.severity == Severity.WARNING for f in findings)

def test_consistent_pattern_no_warning():
    df = pl.DataFrame({"code": ["ABC-123"] * 100})
    findings = PatternConsistencyProfiler().profile(df, "code")
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    assert len(warnings) == 0


def test_generalize_series_matches_python_loop():
    """Vectorised _generalize_series must produce identical output to the
    per-row _generalize across the ASCII-heavy shapes the profiler sees in
    production (phones, zips, codes, names, addresses, mixed nulls). Pins
    the contract so a future regex tweak can't silently diverge.

    Includes the superscript/fraction case (²cubed, ½ pound) — Python's
    str.isdigit returns True for those, and the vectorised version uses
    `[\\d\\p{No}]` to match the same set.
    """
    samples = [
        "abc 123",
        "(555) 123-4567",
        "555.123.4567",
        "12345-6789",
        "ABC-123",
        "John Smith",
        "123 Main St, Apt 4B",
        "user@example.com",
        "",
        None,
    ]
    s = pl.Series("col", samples)
    vec = _generalize_series(s).to_list()
    py = [_generalize(v) if v is not None else None for v in samples]
    assert vec == py, f"mismatch: vec={vec} py={py}"


def test_generalize_series_divergence_on_compat_digits():
    """Documented divergence: Python ``str.isdigit()`` returns True for
    compatibility digits like ``²`` (Numeric_Type=Digit) and False for
    fractions like ``½`` (Numeric_Type=Numeric). The vectorised version
    uses ``\\d`` (= ``\\p{Nd}``, decimal digits only) and treats both ²
    and ½ as non-digits. Pinned here so a future regex tweak documents
    its intent.
    """
    samples = ["²cubed", "½ pound"]
    s = pl.Series("col", samples)
    vec = _generalize_series(s).to_list()
    # ² and ½ both stay as themselves under vectorised; "cubed"/"pound" → letters.
    assert vec == ["²LLLLL", "½ LLLLL"]


def test_generalize_series_letters_before_digits_order():
    """Regression: replacing digits BEFORE letters introduces the literal
    `D` char into the buffer, which the subsequent letter pass then
    re-classifies as a letter (since `D` is ASCII alpha). Letters-first is
    the correct order. This test would fail under the wrong order.
    """
    s = pl.Series("col", ["abc123"])
    assert _generalize_series(s).to_list() == ["LLLDDD"]
