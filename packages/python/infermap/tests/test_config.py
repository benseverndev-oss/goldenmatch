"""Tests for infermap.config.from_config()."""
from __future__ import annotations

import textwrap

import pytest
import yaml

from infermap.config import from_config
from infermap.errors import ConfigError
from infermap.types import MapResult


def _write_yaml(tmp_path, data: dict, filename: str = "mapping.yaml") -> str:
    """Write a YAML file and return its path string."""
    path = tmp_path / filename
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
    return str(path)


class TestFromConfig:
    def test_roundtrip(self, tmp_path):
        """Load a YAML config and verify MapResult fields match."""
        config_data = {
            "version": "1",
            "mappings": [
                {"source": "fname", "target": "first_name", "confidence": 0.95},
                {"source": "lname", "target": "last_name", "confidence": 0.92},
                {"source": "email_addr", "target": "email", "confidence": 0.88},
            ],
            "unmapped_source": ["zipcode"],
            "unmapped_target": ["phone"],
        }
        path = _write_yaml(tmp_path, config_data)

        result = from_config(path)

        assert isinstance(result, MapResult)
        assert len(result.mappings) == 3

        # Verify first mapping
        first = result.mappings[0]
        assert first.source == "fname"
        assert first.target == "first_name"
        assert abs(first.confidence - 0.95) < 1e-6

        # Verify second mapping
        second = result.mappings[1]
        assert second.source == "lname"
        assert second.target == "last_name"
        assert abs(second.confidence - 0.92) < 1e-6

        # Verify unmapped lists
        assert result.unmapped_source == ["zipcode"]
        assert result.unmapped_target == ["phone"]

        # Verify metadata
        assert "loaded_from" in result.metadata
        assert path in result.metadata["loaded_from"] or result.metadata["loaded_from"].endswith("mapping.yaml")

    def test_missing_file_raises(self, tmp_path):
        """ConfigError is raised when the file does not exist."""
        missing = str(tmp_path / "nonexistent.yaml")
        with pytest.raises(ConfigError, match="not found"):
            from_config(missing)

    def test_invalid_yaml_raises(self, tmp_path):
        """ConfigError is raised when the file contains invalid YAML."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            textwrap.dedent("""\
                mappings:
                  - source: fname
                    target: [unclosed bracket
                """),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="[Ii]nvalid YAML|YAML"):
            from_config(str(bad_yaml))

    def test_missing_mappings_key_raises(self, tmp_path):
        """ConfigError is raised when 'mappings' key is absent."""
        config_data = {"version": "1", "other_key": "value"}
        path = _write_yaml(tmp_path, config_data)
        with pytest.raises(ConfigError, match="mappings"):
            from_config(path)

    def test_roundtrip_via_to_config(self, tmp_path):
        """MapResult.to_config() then from_config() produces equivalent data."""
        from infermap.types import FieldMapping, MapResult

        original = MapResult(
            mappings=[
                FieldMapping(source="fname", target="first_name", confidence=0.95),
                FieldMapping(source="lname", target="last_name", confidence=0.90),
            ],
            unmapped_source=["extra_col"],
            unmapped_target=["middle_name"],
        )
        config_path = str(tmp_path / "generated.yaml")
        original.to_config(config_path)

        loaded = from_config(config_path)

        assert len(loaded.mappings) == 2
        sources = [m.source for m in loaded.mappings]
        targets = [m.target for m in loaded.mappings]
        assert "fname" in sources
        assert "first_name" in targets
        assert loaded.unmapped_source == ["extra_col"]
        assert loaded.unmapped_target == ["middle_name"]
        assert "loaded_from" in loaded.metadata
