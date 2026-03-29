"""infermap — inference-driven schema mapping engine."""

__version__ = "0.1.0"

from infermap.types import FieldInfo, FieldMapping, MapResult, SchemaInfo, ScorerResult
from infermap.errors import ApplyError, ConfigError, InferMapError

__all__ = [
    "FieldInfo",
    "FieldMapping",
    "MapResult",
    "SchemaInfo",
    "ScorerResult",
    "ApplyError",
    "ConfigError",
    "InferMapError",
]
