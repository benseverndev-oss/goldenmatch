"""Domain alias dictionaries shipped with infermap.

Each file in this directory is a YAML mapping of canonical field names to
their known aliases for a particular domain. Load via
``infermap.dictionaries.load_domain("healthcare")``.

Format (YAML):
    canonical_name:
      - alias1
      - alias2
      - ...

Domains are additive. Loading multiple domains merges their entries; when
the same canonical appears in both, the alias lists are unioned.

The ``generic`` domain is the default and contains common PII / customer /
order fields. It is loaded automatically by the default ``AliasScorer``.
Domain-specific dictionaries (``healthcare``, ``finance``, ``ecommerce``,
...) are opt-in via ``MapEngine(domains=[...])``.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_DIR = Path(__file__).parent


class UnknownDomainError(ValueError):
    """Raised when a requested domain has no shipped YAML file."""


def available_domains() -> list[str]:
    """Return the sorted list of domain names shipped with infermap."""
    return sorted(p.stem for p in _DIR.glob("*.yaml"))


def load_domain(name: str) -> dict[str, list[str]]:
    """Load a single domain YAML file and return its alias dict.

    Raises ``UnknownDomainError`` if *name* does not correspond to a shipped
    dictionary. Use ``available_domains()`` to see valid names.
    """
    path = _DIR / f"{name}.yaml"
    if not path.exists():
        raise UnknownDomainError(
            f"Unknown domain '{name}'. Available: {available_domains()}"
        )
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Domain '{name}' must be a mapping at the top level")
    # Normalize values to lists of strings.
    result: dict[str, list[str]] = {}
    for canonical, aliases in data.items():
        if not isinstance(canonical, str):
            raise ValueError(f"Domain '{name}': canonical key {canonical!r} is not a string")
        if not isinstance(aliases, list):
            raise ValueError(
                f"Domain '{name}': aliases for '{canonical}' must be a list"
            )
        result[canonical] = [str(a) for a in aliases]
    return result


def merge_domains(names: list[str]) -> dict[str, list[str]]:
    """Load multiple domains and merge them into one alias dict.

    When a canonical name appears in more than one domain, its alias lists
    are unioned (preserving first-seen order).
    """
    merged: dict[str, list[str]] = {}
    for name in names:
        domain = load_domain(name)
        for canonical, aliases in domain.items():
            if canonical not in merged:
                merged[canonical] = list(aliases)
            else:
                existing = merged[canonical]
                for alias in aliases:
                    if alias not in existing:
                        existing.append(alias)
    return merged


__all__ = [
    "UnknownDomainError",
    "available_domains",
    "load_domain",
    "merge_domains",
]
