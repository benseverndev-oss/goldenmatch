"""Load domain packs from yaml files."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from goldencheck_types.types import DomainPack, FieldSpec


class DomainPackError(ValueError):
    """A domain-pack YAML file is malformed (wrong shape, type, or value).

    Distinct from FileNotFoundError (file missing) and KeyError (unknown
    domain name) so callers can react differently — a malformed pack is
    a fix-the-yaml situation, not a fix-the-call situation.
    """


def _domains_dir() -> Path:
    """Resolve the domains/ directory at runtime.

    Order:
    1. Test override via ``GOLDENCHECK_TYPES_TEST_DIR`` env var.
    2. Vendored at ``goldencheck_types/_domains/`` — present both in
       source checkouts and in built wheels / sdists. This is the
       authoritative location for the Python package.
    3. Cross-package monorepo fallback:
       ``packages/typescript/goldencheck-types/domains/``. Only used
       when the vendored copy is absent (e.g. a fresh source checkout
       before ``scripts/sync-domain-packs.py`` has run). Going through
       this path means the YAMLs are NOT in the wheel — every install
       outside the monorepo would break — so the vendored dir should
       always be preferred.
    """
    if override := os.environ.get("GOLDENCHECK_TYPES_TEST_DIR"):
        return Path(override)

    here = Path(__file__).resolve().parent

    bundled = here / "_domains"
    if bundled.exists() and any(bundled.glob("*.yaml")):
        return bundled

    # Source-checkout fallback — useful only inside the monorepo before
    # the vendoring step has run. Production installs always hit the
    # bundled branch above.
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
    """Load and validate a domain pack YAML.

    Shape-checks every field rather than silently coercing. A misindented
    ``name_hints:`` or a string-where-list-expected used to produce a pack
    that "loaded fine" but matched nothing (or matched everything via
    single-character iteration over a string); now it raises
    ``DomainPackError`` with the file path and key path so the user can
    fix the YAML directly.
    """
    path = _domains_dir() / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"domain pack {name!r} not found in {_domains_dir()}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise DomainPackError(f"{path}: empty or null YAML; expected a mapping")
    if not isinstance(raw, dict):
        raise DomainPackError(
            f"{path}: top level must be a mapping, got {type(raw).__name__}"
        )

    types_raw = raw.get("types")
    if types_raw is None:
        types_raw = {}
    elif not isinstance(types_raw, dict):
        raise DomainPackError(
            f"{path}: 'types' must be a mapping, got {type(types_raw).__name__}"
        )

    types: dict[str, FieldSpec] = {}
    for type_name, spec in types_raw.items():
        if not isinstance(spec, dict):
            raise DomainPackError(
                f"{path}: types.{type_name} must be a mapping, got {type(spec).__name__}"
            )

        name_hints = spec.get("name_hints", [])
        if not isinstance(name_hints, list):
            raise DomainPackError(
                f"{path}: types.{type_name}.name_hints must be a list, "
                f"got {type(name_hints).__name__}"
            )

        value_signals = spec.get("value_signals", {})
        if not isinstance(value_signals, dict):
            raise DomainPackError(
                f"{path}: types.{type_name}.value_signals must be a mapping, "
                f"got {type(value_signals).__name__}"
            )

        suppress = spec.get("suppress", [])
        if not isinstance(suppress, list):
            raise DomainPackError(
                f"{path}: types.{type_name}.suppress must be a list, "
                f"got {type(suppress).__name__}"
            )

        threshold = spec.get("confidence_threshold")
        if threshold is not None:
            try:
                threshold_f = float(threshold)
            except (TypeError, ValueError):
                raise DomainPackError(
                    f"{path}: types.{type_name}.confidence_threshold must be numeric, "
                    f"got {threshold!r}"
                )
            if not (0.0 <= threshold_f <= 1.0):
                raise DomainPackError(
                    f"{path}: types.{type_name}.confidence_threshold must be in [0,1], "
                    f"got {threshold!r}"
                )
        else:
            threshold_f = None

        types[type_name] = FieldSpec(
            name_hints=[str(h) for h in name_hints],
            value_signals=dict(value_signals),
            suppress=[str(s) for s in suppress],
            confidence_threshold=threshold_f,
            description=spec.get("description"),
        )

    return DomainPack(
        name=name,
        description=raw.get("description") or "",
        types=types,
    )
