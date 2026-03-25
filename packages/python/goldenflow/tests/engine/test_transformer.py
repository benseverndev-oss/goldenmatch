from pathlib import Path

import polars as pl

# Import all transforms so they register
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.numeric  # noqa: F401
import goldenflow.transforms.address  # noqa: F401
import goldenflow.transforms.names  # noqa: F401

from goldenflow.engine.transformer import TransformEngine, TransformResult
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec


def test_transform_zero_config(sample_csv: Path):
    engine = TransformEngine()
    result = engine.transform_file(sample_csv)
    assert isinstance(result, TransformResult)
    assert isinstance(result.df, pl.DataFrame)
    assert result.manifest is not None
    assert result.df.shape[0] == 3  # same row count


def test_transform_with_config(sample_csv: Path):
    config = GoldenFlowConfig(
        transforms=[
            TransformSpec(column="name", ops=["strip", "title_case"]),
            TransformSpec(column="email", ops=["lowercase", "strip"]),
        ]
    )
    engine = TransformEngine(config=config)
    result = engine.transform_file(sample_csv)
    # name should be stripped and title-cased
    names = result.df["name"].to_list()
    assert names[0] == "John Smith"
    assert names[1] == "Jane Doe"


def test_transform_dataframe():
    df = pl.DataFrame({
        "email": ["  JOHN@TEST.COM  ", "jane@test.com"],
    })
    config = GoldenFlowConfig(
        transforms=[TransformSpec(column="email", ops=["strip", "lowercase"])]
    )
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert result.df["email"].to_list() == ["john@test.com", "jane@test.com"]


def test_transform_with_renames():
    df = pl.DataFrame({"email_address": ["john@test.com"]})
    config = GoldenFlowConfig(renames={"email_address": "email"})
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert "email" in result.df.columns
    assert "email_address" not in result.df.columns


def test_transform_with_drop():
    df = pl.DataFrame({"keep": [1], "remove": [2]})
    config = GoldenFlowConfig(drop=["remove"])
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert "keep" in result.df.columns
    assert "remove" not in result.df.columns


def test_transform_with_dedup():
    df = pl.DataFrame({"email": ["a@test.com", "b@test.com", "a@test.com"], "name": ["A", "B", "C"]})
    from goldenflow.config.schema import DedupSpec
    config = GoldenFlowConfig(dedup=DedupSpec(columns=["email"], keep="first"))
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert result.df.shape[0] == 2


def test_transform_with_filters():
    df = pl.DataFrame({"email": ["a@test.com", None, "c@test.com"]})
    from goldenflow.config.schema import FilterSpec
    config = GoldenFlowConfig(filters=[FilterSpec(column="email", condition="not_null")])
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert result.df.shape[0] == 2


def test_transform_with_splits():
    df = pl.DataFrame({"full_name": ["John Smith", "Jane Doe"]})
    from goldenflow.config.schema import SplitSpec
    config = GoldenFlowConfig(
        splits=[SplitSpec(source="full_name", target=["first_name", "last_name"], method="split_name")]
    )
    engine = TransformEngine(config=config)
    result = engine.transform_df(df)
    assert "first_name" in result.df.columns
    assert "last_name" in result.df.columns
    assert result.df["first_name"].to_list() == ["John", "Jane"]
    assert result.df["last_name"].to_list() == ["Smith", "Doe"]


def test_transform_output_files(sample_csv: Path, tmp_path: Path):
    engine = TransformEngine()
    result = engine.transform_file(sample_csv, output_dir=tmp_path)
    transformed_path = tmp_path / "sample_transformed.csv"
    manifest_path = tmp_path / "sample_manifest.json"
    assert transformed_path.exists()
    assert manifest_path.exists()
