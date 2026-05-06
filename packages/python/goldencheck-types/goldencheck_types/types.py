"""Canonical field-type dataclasses shared across the Golden Suite.

Wire-format contract — these classes ship across package boundaries
(InferMap → GoldenCheck → GoldenPipe) and across language boundaries
(Python ↔ TypeScript). Renaming a field or changing a default is a
breaking change. ``SCHEMA_VERSION`` lets consumers detect mismatches at
runtime if the wire shape ever has to evolve.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Canonical "no mapping found" sentinel for ``FieldMapping.type``. Use
#: ``FieldMapping.is_unknown`` to test rather than comparing this string
#: directly. Keeping it as a module constant means the value is renameable
#: in one place if the contract ever changes.
UNMAPPED_TYPE: str = "unknown"

#: Wire-format version embedded in ``InferredSchema``. Bump on any
#: backwards-incompatible change to the on-the-wire shape (field
#: rename / type change / removed key). Consumers that care can
#: ``raise`` if they see an unexpected version.
SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class FieldSpec:
    """One canonical field type defined by a domain pack."""

    name_hints: list[str]
    value_signals: dict[str, Any]
    suppress: list[str]
    confidence_threshold: float | None = None
    description: str | None = None


@dataclass(frozen=True)
class DomainPack:
    """A named bundle of FieldSpec definitions (e.g., 'finance', 'healthcare')."""

    name: str
    description: str
    types: dict[str, FieldSpec]


@dataclass(frozen=True)
class FieldMapping:
    """One source column's mapping to a canonical type, or unmapped.

    Frozen because this travels across the wire (InferMap → GoldenCheck);
    mutating it after the fact would mean the InferredSchema you
    serialized doesn't match the one downstream consumers act on.
    """

    source_col: str
    canonical: str | None
    type: str  # canonical type name, or UNMAPPED_TYPE for "unknown"
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    # evidence is InferMap-internal; consumers must not depend on its shape.

    @property
    def is_unknown(self) -> bool:
        return self.type == UNMAPPED_TYPE


@dataclass(frozen=True)
class InferredSchema:
    """Result of running InferMap with a domain pack as target."""

    domain: str
    fields: dict[str, FieldMapping]
    confidence: float
    schema_version: int = SCHEMA_VERSION

    @property
    def unmapped(self) -> list[str]:
        return [k for k, v in self.fields.items() if v.is_unknown]
