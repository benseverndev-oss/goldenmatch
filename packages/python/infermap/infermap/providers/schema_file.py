"""SchemaFileProvider — reads YAML/JSON schema definition files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from infermap.errors import ConfigError
from infermap.types import FieldInfo, SchemaInfo


class SchemaFileProvider:
    """Reads a YAML or JSON file describing a schema and returns SchemaInfo."""

    def extract(self, source: Any, **kwargs) -> SchemaInfo:
        path = Path(source)
        suffix = path.suffix.lower()

        if suffix in (".yaml", ".yml"):
            with open(path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
        elif suffix == ".json":
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        else:
            raise ConfigError(f"Unsupported schema file format: {suffix}")

        if not isinstance(raw, dict) or "fields" not in raw:
            raise ConfigError(
                f"Schema file must contain a top-level 'fields' key: {path}"
            )

        fields: list[FieldInfo] = []
        required_fields: list[str] = []

        for entry in raw["fields"]:
            name = entry["name"]
            dtype = entry.get("dtype", "string") or "string"
            aliases = entry.get("aliases", [])
            is_required = bool(entry.get("required", False))

            metadata: dict = {}
            if aliases:
                metadata["aliases"] = list(aliases)

            fields.append(
                FieldInfo(
                    name=name,
                    dtype=dtype,
                    sample_values=[],
                    null_rate=0.0,
                    unique_rate=0.0,
                    value_count=0,
                    metadata=metadata,
                )
            )

            if is_required:
                required_fields.append(name)

        return SchemaInfo(
            fields=fields,
            source_name=path.stem,
            required_fields=required_fields,
        )
