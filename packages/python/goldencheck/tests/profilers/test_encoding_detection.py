"""Tests for EncodingDetectionProfiler."""
import polars as pl
from goldencheck.profilers.encoding_detection import EncodingDetectionProfiler


def test_zero_width_chars_detected():
    df = pl.DataFrame({"name": ["Alice", "Bob\u200B", "Charlie"]})
    findings = EncodingDetectionProfiler().profile(df, "name")
    assert any("zero-width" in f.message.lower() for f in findings)


def test_smart_quotes_detected():
    df = pl.DataFrame({"desc": ["he said \u201Chello\u201D", "normal text", "another"]})
    findings = EncodingDetectionProfiler().profile(df, "desc")
    assert any("smart quote" in f.message.lower() or "curly" in f.message.lower() for f in findings)


def test_clean_ascii_no_findings():
    df = pl.DataFrame({"name": ["Alice", "Bob", "Charlie"] * 50})
    findings = EncodingDetectionProfiler().profile(df, "name")
    encoding_findings = [f for f in findings if f.check == "encoding_detection"]
    assert len(encoding_findings) == 0


def test_non_numeric_column_only():
    df = pl.DataFrame({"count": [1, 2, 3]})
    findings = EncodingDetectionProfiler().profile(df, "count")
    assert len(findings) == 0


def test_non_ascii_detected():
    df = pl.DataFrame({"name": ["caf\u00E9", "na\u00EFve", "normal"]})
    findings = EncodingDetectionProfiler().profile(df, "name")
    assert any(f.check == "encoding_detection" for f in findings)


def test_control_chars_detected():
    df = pl.DataFrame({"data": ["hello\x01world", "normal", "text"]})
    findings = EncodingDetectionProfiler().profile(df, "data")
    assert any("control" in f.message.lower() for f in findings)


def test_zero_width_and_smart_quotes_detected_arrow_path():
    """Regression: on the Arrow scan path (``scan_dataframe(pa.Table)``) the
    zero-width / smart-quote patterns run through Polars' Rust regex, which
    rejects ``\\uXXXX`` escapes with ``invalid escape sequence: \\u`` -- so the
    profiler raised on EVERY string column and detection was silently dead.
    Building the char classes from literal chars fixes it."""
    import pyarrow as pa

    tbl = pa.table({"c": ["clean", "bad​value", "he‘llo", "normal"]})
    findings = EncodingDetectionProfiler().profile(tbl, "c")
    messages = [f.message.lower() for f in findings]
    assert any("zero-width" in m for m in messages)
    assert any("smart quote" in m or "curly" in m for m in messages)
    # clean-only Arrow column: no error, no false positive
    clean = pa.table({"c": ["ann", "bob", "cara"]})
    assert EncodingDetectionProfiler().profile(clean, "c") == []


def test_zero_width_confidence():
    df = pl.DataFrame({"name": ["Alice", "Bob\u200B", "Charlie"]})
    findings = EncodingDetectionProfiler().profile(df, "name")
    zw_findings = [f for f in findings if "zero-width" in f.message.lower()]
    assert all(f.confidence == 0.8 for f in zw_findings)


def test_smart_quote_confidence():
    df = pl.DataFrame({"desc": ["he said \u201Chello\u201D", "normal text", "another"]})
    findings = EncodingDetectionProfiler().profile(df, "desc")
    sq_findings = [f for f in findings if "smart quote" in f.message.lower() or "curly" in f.message.lower()]
    assert all(f.confidence == 0.6 for f in sq_findings)
