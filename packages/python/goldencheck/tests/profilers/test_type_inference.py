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

def test_minority_numeric_in_text_column():
    # 3 numbers out of 100 strings — should flag as low confidence
    values = [f"Name{i}" for i in range(97)] + ["12345", "99999", "11111"]
    df = pl.DataFrame({"last_name": values})
    findings = TypeInferenceProfiler().profile(df, "last_name")
    assert len(findings) > 0
    assert any(f.confidence < 0.5 for f in findings)

def test_type_inference_writes_context():
    df = pl.DataFrame({"age": ["25", "30", "45", "28", "33"]})
    context = {}
    TypeInferenceProfiler().profile(df, "age", context=context)
    assert context.get("age", {}).get("mostly_numeric") is True

def test_type_inference_existing_behavior_unchanged():
    # >80% numeric still works as before
    df = pl.DataFrame({"age": ["25", "30", "forty-five", "28", "33"]})
    findings = TypeInferenceProfiler().profile(df, "age")
    assert any(f.confidence >= 0.8 for f in findings)
