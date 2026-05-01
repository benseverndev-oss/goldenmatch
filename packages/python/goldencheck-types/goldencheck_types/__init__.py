"""goldencheck-types — shared canonical field types for the Golden Suite."""
from __future__ import annotations

from goldencheck_types.types import (
    DomainPack,
    FieldMapping,
    FieldSpec,
    InferredSchema,
)
from goldencheck_types.loader import list_domains, load_domain

__version__ = "0.1.0"
__all__ = [
    "DomainPack",
    "FieldMapping",
    "FieldSpec",
    "InferredSchema",
    "list_domains",
    "load_domain",
    "__version__",
]
