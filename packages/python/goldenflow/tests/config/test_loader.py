from pathlib import Path

import yaml

from goldenflow.config.loader import load_config, merge_configs
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec


def test_load_config_from_yaml(tmp_path: Path):
    config_path = tmp_path / "goldenflow.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "source": "data.csv",
                "output": "clean.csv",
                "transforms": [{"column": "name", "ops": ["strip", "title_case"]}],
                "renames": {"email_address": "email"},
                "drop": ["internal_id"],
            }
        )
    )
    config = load_config(config_path)
    assert config.source == "data.csv"
    assert len(config.transforms) == 1
    assert config.drop == ["internal_id"]


def test_load_config_missing_file():
    config = load_config(Path("nonexistent.yaml"))
    assert config == GoldenFlowConfig()


def test_merge_configs_cli_overrides_file():
    file_config = GoldenFlowConfig(source="file.csv", output="out.csv")
    cli_overrides = GoldenFlowConfig(source="override.csv")
    merged = merge_configs(file_config, cli_overrides)
    assert merged.source == "override.csv"
    assert merged.output == "out.csv"


def test_merge_configs_lists_replaced():
    file_config = GoldenFlowConfig(
        transforms=[TransformSpec(column="a", ops=["strip"])]
    )
    cli_overrides = GoldenFlowConfig(
        transforms=[TransformSpec(column="b", ops=["lowercase"])]
    )
    merged = merge_configs(file_config, cli_overrides)
    assert len(merged.transforms) == 1
    assert merged.transforms[0].column == "b"
