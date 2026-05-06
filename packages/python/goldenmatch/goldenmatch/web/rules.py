from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_THRESHOLD = 0.85


def load_rules_from_yaml(config_path: Path | None) -> dict:
    """Read matchkey + threshold out of a goldenmatch.yml.

    Tolerant: missing or empty file returns the empty-rules shape rather than
    raising. Used by both the read-only project route (Task 2) and the editable
    rules route (Task 4) so YAML key normalization stays in one place.

    Accepts both `matchkey` (singular, canonical in goldenmatch.yml) and
    `matchkeys` (plural, the wire shape preferred by the web UI).
    """
    if config_path is None or not config_path.exists():
        return {"threshold": DEFAULT_THRESHOLD, "matchkeys": [], "standardization": None}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    threshold = raw.get("threshold")
    return {
        "threshold": float(threshold) if threshold is not None else DEFAULT_THRESHOLD,
        "matchkeys": raw.get("matchkey") or raw.get("matchkeys") or [],
        "standardization": _extract_standardization(raw),
    }


def _extract_standardization(raw: dict) -> dict[str, list[str]] | None:
    """Pull the standardization block out of a raw YAML dict.

    The engine's loader accepts two shapes:
      standardization:        # shorthand
        email: [email]
      standardization:        # explicit
        rules:
          email: [email]

    Both flatten to the same column-keyed dict here.
    """
    std = raw.get("standardization")
    if not isinstance(std, dict) or not std:
        return None
    if "rules" in std and isinstance(std["rules"], dict):
        rules = std["rules"]
    else:
        rules = std
    out: dict[str, list[str]] = {}
    for col, names in rules.items():
        if isinstance(names, list):
            out[col] = [str(n) for n in names]
    return out or None
