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
        return {"threshold": DEFAULT_THRESHOLD, "matchkeys": []}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    threshold = raw.get("threshold")
    return {
        "threshold": float(threshold) if threshold is not None else DEFAULT_THRESHOLD,
        "matchkeys": raw.get("matchkey") or raw.get("matchkeys") or [],
    }
