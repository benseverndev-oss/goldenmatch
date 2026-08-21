"""goldencheck-types — shared canonical field types for the Golden Suite."""
from __future__ import annotations

from goldencheck_types.loader import (
    DomainPackError,
    clear_cache,
    list_domains,
    load_domain,
)
from goldencheck_types.types import (
    IDENTITY_KINDS,
    LAYER_REASONS,
    SCHEMA_VERSION,
    UNKNOWN_ROLE,
    UNMAPPED_TYPE,
    DetectionResult,
    DomainPack,
    FieldGroupSpec,
    FieldMapping,
    FieldSpec,
    IdentityLayer,
    InferredSchema,
    LayerDetectionResult,
    RoleSpec,
    is_unknown,
    unmapped_cols,
)

__version__ = "0.3.0"
__all__ = [
    "DetectionResult",
    "DomainPack",
    "DomainPackError",
    "FieldGroupSpec",
    "FieldMapping",
    "FieldSpec",
    "IDENTITY_KINDS",
    "IdentityLayer",
    "InferredSchema",
    "LAYER_REASONS",
    "LayerDetectionResult",
    "RoleSpec",
    "SCHEMA_VERSION",
    "UNKNOWN_ROLE",
    "UNMAPPED_TYPE",
    "clear_cache",
    "is_unknown",
    "list_domains",
    "load_domain",
    "unmapped_cols",
    "__version__",
]
