"""Tests for config models and YAML loader."""
import pytest
from goldenpipe.models.config import StageSpec, PipelineConfig
from goldenpipe.config.loader import load_config  # noqa: E402


class TestStageSpec:
    def test_minimal(self):
        s = StageSpec(use="goldencheck.scan")
        assert s.use == "goldencheck.scan"
        assert s.name is None
        assert s.needs == []
        assert s.skip_if is None
        assert s.on_error == "continue"
        assert s.config == {}

    def test_rich(self):
        s = StageSpec(
            name="validate",
            use="goldencheck.scan",
            needs=["load"],
            skip_if="findings",
            on_error="abort",
            config={"severity_threshold": "warning"},
        )
        assert s.name == "validate"
        assert s.on_error == "abort"
        assert s.config["severity_threshold"] == "warning"

    def test_on_error_validation(self):
        with pytest.raises(Exception):
            StageSpec(use="test", on_error="invalid")


class TestPipelineConfig:
    def test_minimal_string_stages(self):
        c = PipelineConfig(
            pipeline="test",
            stages=["goldencheck.scan", "goldenflow.transform"],
        )
        assert c.pipeline == "test"
        assert len(c.stages) == 2
        assert c.source is None
        assert c.decisions == []

    def test_mixed_stages(self):
        c = PipelineConfig(
            pipeline="test",
            stages=[
                "goldencheck.scan",
                StageSpec(name="clean", use="goldenflow.transform"),
            ],
        )
        assert isinstance(c.stages[0], str)
        assert isinstance(c.stages[1], StageSpec)

    def test_rich_config(self):
        c = PipelineConfig(
            pipeline="customers-dedupe",
            source="customers.csv",
            output="golden.csv",
            stages=[
                StageSpec(
                    name="validate",
                    use="goldencheck.scan",
                    config={"severity_threshold": "warning"},
                ),
            ],
            decisions=["severity_gate", "pii_router"],
        )
        assert c.source == "customers.csv"
        assert c.output == "golden.csv"
        assert len(c.decisions) == 2


# Task 5: Config Loader tests


class TestLoadConfig:
    def test_load_minimal_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text(
            "pipeline: test\n"
            "stages:\n"
            "  - goldencheck.scan\n"
            "  - goldenflow.transform\n"
        )
        config = load_config(str(f))
        assert config.pipeline == "test"
        assert len(config.stages) == 2
        assert all(isinstance(s, StageSpec) for s in config.stages)

    def test_load_rich_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text(
            "pipeline: test\n"
            "source: data.csv\n"
            "stages:\n"
            "  - name: validate\n"
            "    use: goldencheck.scan\n"
            "    config:\n"
            "      severity_threshold: warning\n"
            "decisions:\n"
            "  - severity_gate\n"
        )
        config = load_config(str(f))
        assert config.source == "data.csv"
        assert config.stages[0].name == "validate"
        assert config.decisions == ["severity_gate"]

    def test_load_mixed_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text(
            "pipeline: test\n"
            "stages:\n"
            "  - goldencheck.scan\n"
            "  - name: clean\n"
            "    use: goldenflow.transform\n"
        )
        config = load_config(str(f))
        assert config.stages[0].use == "goldencheck.scan"
        assert config.stages[1].name == "clean"

    def test_load_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/pipe.yml")

    def test_load_invalid_yaml(self, tmp_path):
        f = tmp_path / "pipe.yml"
        f.write_text("pipeline: test\nstages: not_a_list\n")
        with pytest.raises(Exception):
            load_config(str(f))
