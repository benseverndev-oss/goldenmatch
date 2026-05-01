import polars as pl
from goldencheck.profilers.cardinality import CardinalityProfiler

def test_low_cardinality_suggests_enum():
    df = pl.DataFrame({"status": ["active", "inactive", "pending", "closed"] * 25})
    findings = CardinalityProfiler().profile(df, "status")
    assert any("enum" in f.message.lower() for f in findings)

def test_high_cardinality_no_enum():
    df = pl.DataFrame({"name": [f"Person {i}" for i in range(500)]})
    findings = CardinalityProfiler().profile(df, "name")
    assert not any("enum" in f.message.lower() for f in findings)
