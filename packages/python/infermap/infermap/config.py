"""infermap config — load saved mapping configurations."""
from __future__ import annotations

from pathlib import Path

import yaml

from infermap.errors import ConfigError
from infermap.types import FieldMapping, MapResult


def from_config(path: str) -> MapResult:
    """Load a saved YAML mapping config and return a MapResult.

    Parameters
    ----------
    path:
        Path to the YAML config file (produced by ``MapResult.to_config``).

    Returns
    -------
    MapResult
        A MapResult reconstructed from the saved config, with ``metadata["loaded_from"]``
        set to the resolved file path.

    Raises
    ------
    ConfigError
        If the file does not exist, contains invalid YAML, or is missing the
        required ``"mappings"`` key.
    """
    cfg_path = Path(path)

    if not cfg_path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config file {path}: {exc}") from exc

    if not isinstance(data, dict) or "mappings" not in data:
        raise ConfigError(f"Config file {path} is missing required 'mappings' key.")

    raw_mappings = data["mappings"]
    if not isinstance(raw_mappings, list):
        raise ConfigError(f"Config file {path}: 'mappings' must be a list.")

    mappings: list[FieldMapping] = []
    for entry in raw_mappings:
        if not isinstance(entry, dict):
            raise ConfigError(f"Config file {path}: each mapping entry must be a dict.")
        source = entry.get("source", "")
        target = entry.get("target", "")
        confidence = float(entry.get("confidence", 0.0))
        mappings.append(FieldMapping(source=source, target=target, confidence=confidence))

    unmapped_source: list[str] = data.get("unmapped_source") or []
    unmapped_target: list[str] = data.get("unmapped_target") or []

    metadata = {
        "loaded_from": str(cfg_path.resolve()),
        "version": data.get("version", ""),
    }

    return MapResult(
        mappings=mappings,
        unmapped_source=unmapped_source,
        unmapped_target=unmapped_target,
        metadata=metadata,
    )
