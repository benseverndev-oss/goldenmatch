"""goldencheck-types — shared canonical field types for the Golden Suite."""
from __future__ import annotations

from goldencheck_types.loader import (
    DomainPackError,
    clear_cache,
    list_domains,
    load_domain,
)
from goldencheck_types.types import (
    SCHEMA_VERSION,
    UNMAPPED_TYPE,
    DetectionResult,
    DomainPack,
    FieldMapping,
    FieldSpec,
    InferredSchema,
    is_unknown,
    unmapped_cols,
)

__version__ = "0.1.0"
__all__ = [
    "DetectionResult",
    "DomainPack",
    "DomainPackError",
    "FieldMapping",
    "FieldSpec",
    "InferredSchema",
    "SCHEMA_VERSION",
    "UNMAPPED_TYPE",
    "clear_cache",
    "is_unknown",
    "list_domains",
    "load_domain",
    "unmapped_cols",
    "__version__",
]
