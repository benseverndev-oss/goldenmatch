"""Load domain packs from yaml files."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from goldencheck_types.types import DomainPack, FieldSpec


def _domains_dir() -> Path:
    """Resolve the domains/ directory at runtime.

    Order:
    1. Test override via ``GOLDENCHECK_TYPES_TEST_DIR`` env var.
    2. Bundled with installed wheel: ``<pkg>/_domains/``.
    3. Source layout (monorepo dev):
       ``packages/typescript/goldencheck-types/domains/``.
    """
    if override := os.environ.get("GOLDENCHECK_TYPES_TEST_DIR"):
        return Path(override)

    here = Path(__file__).resolve().parent

    bundled = here / "_domains"
    if bundled.exists():
        return bundled

    # packages/python/goldencheck-types/goldencheck_types/loader.py
    #   -> packages/typescript/goldencheck-types/domains/
    source_layout = (
        here.parent.parent.parent
        / "typescript"
        / "goldencheck-types"
        / "domains"
    )
    if source_layout.exists():
        return source_layout

    raise FileNotFoundError(f"Could not locate domains/ near {here}")


def list_domains() -> list[str]:
    return sorted(p.stem for p in _domains_dir().glob("*.yaml"))


def load_domain(name: str) -> DomainPack:
    path = _domains_dir() / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"domain pack {name!r} not found in {_domains_dir()}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    types: dict[str, FieldSpec] = {}
    for type_name, spec in (raw.get("types") or {}).items():
        threshold = spec.get("confidence_threshold")
        if threshold is not None and not (0.0 <= float(threshold) <= 1.0):
            raise ValueError(
                f"confidence_threshold for {name}.{type_name} must be in [0,1], "
                f"got {threshold!r}"
            )
        types[type_name] = FieldSpec(
            name_hints=list(spec.get("name_hints") or []),
            value_signals=dict(spec.get("value_signals") or {}),
            suppress=list(spec.get("suppress") or []),
            confidence_threshold=float(threshold) if threshold is not None else None,
            description=spec.get("description"),
        )

    return DomainPack(
        name=name,
        description=raw.get("description", "") or "",
        types=types,
    )
