"""goldencheck-types — shared canonical field types for the Golden Suite."""
from __future__ import annotations

from goldencheck_types.types import (
    SCHEMA_VERSION,
    UNMAPPED_TYPE,
    DomainPack,
    FieldMapping,
    FieldSpec,
    InferredSchema,
)
from goldencheck_types.loader import DomainPackError, list_domains, load_domain

__version__ = "0.1.0"
__all__ = [
    "DomainPack",
    "DomainPackError",
    "FieldMapping",
    "FieldSpec",
    "InferredSchema",
    "SCHEMA_VERSION",
    "UNMAPPED_TYPE",
    "list_domains",
    "load_domain",
    "__version__",
]
