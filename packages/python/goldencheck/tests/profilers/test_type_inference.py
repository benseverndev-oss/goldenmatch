import polars as pl
from goldencheck.profilers.type_inference import TypeInferenceProfiler
from goldencheck.models.finding import Severity

def test_clean_integer_column():
    df = pl.DataFrame({"age": [25, 30, 45, 28, 33]})
    findings = TypeInferenceProfiler().profile(df, "age")
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0

def test_mixed_type_column():
    df = pl.DataFrame({"age": ["25", "30", "forty-five", "28", "33"]})
    findings = TypeInferenceProfiler().profile(df, "age")
    assert len(findings) > 0
    assert any("integer" in f.message.lower() or "numeric" in f.message.lower() for f in findings)

def test_all_string_column():
    df = pl.DataFrame({"name": ["Alice", "Bob", "Charlie"]})
    findings = TypeInferenceProfiler().profile(df, "name")
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert len(errors) == 0
