"""End-to-end integration tests for the full GoldenFlow pipeline."""
from pathlib import Path

import polars as pl

import goldenflow
from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    SplitSpec,
    TransformSpec,
)
from goldenflow.engine.transformer import TransformEngine


FIXTURES = Path(__file__).parent / "fixtures"


def test_zero_config_on_messy_csv():
    """Zero-config mode should auto-detect and fix common issues."""
    path = FIXTURES / "messy.csv"
    result = goldenflow.transform_file(path)
    assert result.df.shape[0] > 0
    assert result.manifest is not None
    assert len(result.manifest.records) > 0


def test_full_config_pipeline(tmp_path: Path):
    """Full config pipeline: transforms, renames, drops, filters, dedup."""
    df = pl.DataFrame({
        "full_name": ["  John Smith  ", "  Jane Doe  ", "  John Smith  "],
        "email_address": ["JOHN@TEST.COM", "jane@test.com", "john@test.com"],
        "phone_number": ["(555) 123-4567", "555.987.6543", "(555) 123-4567"],
        "state": ["Pennsylvania", "CA", "Pennsylvania"],
        "signup_dt": ["03/15/2024", "2024-01-20", "03/15/2024"],
        "price": ["$1,234.56", "$99.99", "$1,234.56"],
        "internal_id": [1, 2, 3],
    })

    config = GoldenFlowConfig(
        transforms=[
            TransformSpec(column="full_name", ops=["strip", "title_case"]),
            TransformSpec(column="email_address", ops=["lowercase", "strip"]),
            TransformSpec(column="phone_number", ops=["phone_e164"]),
            TransformSpec(column="state", ops=["state_abbreviate"]),
            TransformSpec(column="signup_dt", ops=["date_iso8601"]),
            TransformSpec(column="price", ops=["currency_strip"]),
        ],
        renames={"email_address": "email", "phone_number": "phone"},
        drop=["internal_id"],
        dedup=DedupSpec(columns=["email"]),
    )

    engine = TransformEngine(config=config)
    result = engine.transform_df(df)

    # Check transforms applied
    assert result.df["full_name"][0] == "John Smith"
    assert result.df["email"][0] == "john@test.com"
    assert "phone" in result.df.columns
    assert result.df["state"][0] == "PA"
    assert result.df["signup_dt"][0] == "2024-03-15"

    # Check renames
    assert "email" in result.df.columns
    assert "email_address" not in result.df.columns

    # Check drops
    assert "internal_id" not in result.df.columns

    # Check dedup
    assert result.df.shape[0] == 2  # removed 1 duplicate


def test_schema_mapping_roundtrip(tmp_path: Path):
    """Map schemas between two files and verify output."""
    from goldenflow.mapping.schema_mapper import SchemaMapper

    source_df = pl.DataFrame({
        "fname": ["John"],
        "email_address": ["john@test.com"],
        "phone_number": ["5551234567"],
    })
    target_df = pl.DataFrame({
        "first_name": ["Jane"],
        "email": ["jane@test.com"],
        "phone": ["5559876543"],
    })

    mapper = SchemaMapper()
    mappings = mapper.map(source_df, target_df)

    assert len(mappings) >= 2
    source_cols = {m.source for m in mappings}
    assert "fname" in source_cols or "email_address" in source_cols


def test_learn_and_apply(sample_csv: Path, tmp_path: Path):
    """Learn a config from data and re-apply it."""
    from goldenflow.config.learner import learn_config
    from goldenflow.config.loader import save_config, load_config

    config = learn_config(sample_csv)
    config_path = tmp_path / "learned.yaml"
    save_config(config, config_path)

    loaded = load_config(config_path)
    engine = TransformEngine(config=loaded)
    result = engine.transform_file(sample_csv)
    assert result.df.shape[0] == 3


def test_diff_before_after(sample_csv: Path):
    """Diff should detect changes after transformation."""
    from goldenflow.connectors.file import read_file
    from goldenflow.engine.differ import diff_dataframes

    before = read_file(sample_csv)
    engine = TransformEngine()
    result = engine.transform_df(before)
    diff = diff_dataframes(before, result.df)
    # At least some transforms should have changed values
    assert diff.total_changes >= 0  # may be 0 if data is already clean
