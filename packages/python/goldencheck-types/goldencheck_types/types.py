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
#:
#: v2 (2026-05-06): ``FieldSpec`` gained ``name`` so the canonical
#: identifier travels with the spec instead of only as a dict key.
SCHEMA_VERSION: int = 2


@dataclass(frozen=True)
class FieldSpec:
    """One canonical field type defined by a domain pack.

    ``name`` is the canonical identifier (matches the key under
    ``DomainPack.types``). The loader populates it from the dict key and
    raises ``DomainPackError`` if a YAML explicitly sets a different
    name. Carrying the name on the spec lets callers pass a single
    ``FieldSpec`` around without losing identity.
    """

    name: str
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


# ── Predicate parity with the TS sibling ──────────────────────────────────
#
# TS has free functions ``isUnknown(m)`` / ``unmappedCols(s)``. Python's
# original API was the method/property pair ``m.is_unknown`` /
# ``s.unmapped``. Both shapes coexist now: free-function predicates are
# the preferred cross-language form; the methods/properties remain for
# callers that already use them. Don't introduce new code that uses the
# property form — prefer the free functions.


def is_unknown(m: FieldMapping) -> bool:
    """True iff the mapping points at the canonical "no mapping" sentinel.

    Mirrors TS ``isUnknown(m)``. Equivalent to ``m.is_unknown``.
    """
    return m.is_unknown


def unmapped_cols(s: InferredSchema) -> list[str]:
    """Return column names InferMap couldn't type for this schema.

    Mirrors TS ``unmappedCols(s)``. Equivalent to ``s.unmapped``.
    """
    return s.unmapped


# ── Detection ────────────────────────────────────────────────────────────

# Reasons detect_domain_detailed picked (or refused to pick) a domain.
# Useful for callers that want to log "fell back because we tied" vs
# "no candidate scored high enough" vs "no input data" — today the
# str|None return throws all those cases together.
DetectionReason = str  # one of: "confident" | "tie" | "below_min_score" | "no_data"


@dataclass(frozen=True)
class DetectionResult:
    """Rich result of domain auto-detection.

    Use ``detect_domain_detailed`` (returns this) when you want to see
    the runner-up, the score, or distinguish "tied" from "no match".
    The thin ``detect_domain`` wrapper returns just ``.domain`` for
    callers that only care about the picked name.
    """

    domain: str | None
    score: float
    runner_up: str | None
    runner_up_score: float
    reason: DetectionReason
