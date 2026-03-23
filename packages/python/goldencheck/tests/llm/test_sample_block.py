import polars as pl
from goldencheck.llm.sample_block import build_sample_blocks
from goldencheck.models.finding import Finding, Severity

def test_sample_block_contains_metadata():
    df = pl.DataFrame({"name": ["Alice", "Bob", None, "Charlie", "Diana"] * 20})
    findings = [Finding(severity=Severity.WARNING, column="name", check="nullability", message="has nulls")]
    blocks = build_sample_blocks(df, findings)
    assert "name" in blocks
    block = blocks["name"]
    assert "column" in block
    assert block["column"] == "name"
    assert "row_count" in block
    assert block["row_count"] == 100
    assert "null_count" in block

def test_sample_block_contains_values():
    df = pl.DataFrame({"status": ["active"] * 90 + ["inactive"] * 8 + ["UNKNOWN"] * 2})
    blocks = build_sample_blocks(df, [])
    block = blocks["status"]
    assert "top_values" in block
    assert "rare_values" in block
    assert len(block["top_values"]) <= 5

def test_sample_block_includes_flagged_values():
    df = pl.DataFrame({"email": ["a@b.com"] * 100})
    findings = [Finding(severity=Severity.WARNING, column="email", check="format",
                        message="bad", sample_values=["not-email", "also-bad"])]
    blocks = build_sample_blocks(df, findings)
    assert "not-email" in blocks["email"]["flagged_values"]

def test_sample_block_includes_findings():
    df = pl.DataFrame({"age": list(range(100))})
    findings = [Finding(severity=Severity.WARNING, column="age", check="range", message="outliers detected")]
    blocks = build_sample_blocks(df, findings)
    assert len(blocks["age"]["existing_findings"]) == 1

def test_wide_dataset_limited_to_50():
    data = {f"col_{i}": list(range(100)) for i in range(100)}
    df = pl.DataFrame(data)
    findings = [Finding(severity=Severity.ERROR, column="col_0", check="x", message="y")]
    blocks = build_sample_blocks(df, findings, max_columns=50)
    assert len(blocks) == 50
    assert "col_0" in blocks  # column with findings should be included
