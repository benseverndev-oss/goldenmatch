from pathlib import Path
import yaml
from goldencheck.config.loader import load_config
from goldencheck.config.writer import save_config
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, Settings

def test_load_nonexistent_returns_none():
    result = load_config(Path("/nonexistent/goldencheck.yml"))
    assert result is None

def test_load_valid_yaml(tmp_path):
    config_data = {"version": 1, "settings": {"fail_on": "warning"},
                   "columns": {"age": {"type": "integer", "required": True}}}
    path = tmp_path / "goldencheck.yml"
    path.write_text(yaml.dump(config_data))
    config = load_config(path)
    assert config is not None
    assert config.settings.fail_on == "warning"
    assert "age" in config.columns

def test_roundtrip(tmp_path):
    config = GoldenCheckConfig(
        settings=Settings(fail_on="error"),
        columns={"email": ColumnRule(type="string", required=True, format="email")},
    )
    path = tmp_path / "goldencheck.yml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded is not None
    assert loaded.columns["email"].format == "email"
