"""YAML config loader for GoldenMatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from goldenmatch.config.schemas import GoldenMatchConfig


# Schema field names of GoldenRulesConfig, DERIVED not transcribed (#2454).
#
# A hand-maintained literal drifted from the schema three times, each time
# after it bit someone: max_cluster_size, then field_groups /
# field_group_detection. By the fourth report it held 5 of 13 fields, so the
# other 8 were swept into `field_rules` as if they named data columns -- seven
# raised on reload, and `default` (itself rule-shaped) validated and loaded
# SILENTLY WRONG as a column named "default".
#
# Deriving the set means a field 14 is protected the day it is added.
def _golden_rules_field_names() -> frozenset[str]:
    from goldenmatch.config.schemas import GoldenRulesConfig

    return frozenset(GoldenRulesConfig.model_fields)


def _value_matches_schema_field(field_name: str, value: Any) -> bool:
    """True when `value` validates against `field_name`'s DECLARED type.

    This is what lets the schema keep its names without stealing them from
    data columns. The original loader omitted keys like `adaptive` on purpose,
    so a column named `adaptive` carrying a rule would still work -- the cost
    was that the real schema field could not be loaded at all. Type-direction
    keeps both: `adaptive: true` is a bool so it is the schema field;
    `adaptive: {strategy: most_complete}` is a mapping that `bool` rejects, so
    it stays a column rule. The two shapes are disjoint at every schema name.
    """
    from pydantic import TypeAdapter

    from goldenmatch.config.schemas import GoldenRulesConfig

    field = GoldenRulesConfig.model_fields.get(field_name)
    if field is None or field.annotation is None:
        return False
    try:
        TypeAdapter(field.annotation).validate_python(value)
    except Exception:  # noqa: BLE001 - any validation failure means "not this field"
        return False
    return True


def _normalize_golden_rules(raw: dict[str, Any]) -> dict[str, Any]:
    """Move column-rule keys in golden_rules into the field_rules dict.

    A key is kept as a schema field when it names one AND its value validates
    against that field's declared type; otherwise it is swept as a column rule.
    """
    golden = raw.get("golden_rules")
    if golden is None or not isinstance(golden, dict):
        return raw

    schema_names = _golden_rules_field_names()
    field_rules: dict[str, Any] = golden.pop("field_rules", {})
    extra_keys = [
        k
        for k in golden
        if k not in schema_names or not _value_matches_schema_field(k, golden[k])
    ]
    for key in extra_keys:
        field_rules[key] = golden.pop(key)

    if field_rules:
        golden["field_rules"] = field_rules

    return raw


def _normalize_standardization(raw: dict[str, Any]) -> dict[str, Any]:
    """Allow flat standardization format without explicit 'rules' key.

    Users can write either:
        standardization:
          rules:
            email: [email]
    Or the shorthand:
        standardization:
          email: [email]
    """
    std = raw.get("standardization")
    if std is None or not isinstance(std, dict):
        return raw
    if "rules" not in std:
        # Everything is a column->standardizers mapping
        raw["standardization"] = {"rules": std}
    return raw


def load_config(path: str | Path) -> GoldenMatchConfig:
    """Load and validate a GoldenMatch YAML config file.

    Args:
        path: Path to the YAML config file.

    Returns:
        Validated GoldenMatchConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the YAML is invalid or fails validation.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    text = path.read_text(encoding="utf-8")

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got {type(raw).__name__}")

    raw = _normalize_golden_rules(raw)
    raw = _normalize_standardization(raw)

    try:
        return GoldenMatchConfig(**raw)
    except Exception as exc:
        raise ValueError(f"Config validation failed: {exc}") from exc
