"""Canonical field-type dataclasses shared across the Golden Suite."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class FieldMapping:
    """One source column's mapping to a canonical type, or 'unknown'."""

    source_col: str
    canonical: str | None
    type: str  # canonical type name or "unknown"
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    # evidence is InferMap-internal; consumers must not depend on its shape.

    @property
    def is_unknown(self) -> bool:
        return self.type == "unknown"


@dataclass
class InferredSchema:
    """Result of running InferMap with a domain pack as target."""

    domain: str
    fields: dict[str, FieldMapping]
    confidence: float

    @property
    def unmapped(self) -> list[str]:
        return [k for k, v in self.fields.items() if v.is_unknown]
