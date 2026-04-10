"""infermap — inference-driven schema mapping engine."""

__version__ = "0.3.1"

from infermap.types import FieldInfo, FieldMapping, MapResult, SchemaInfo, ScorerResult
from infermap.errors import ApplyError, ConfigError, InferMapError
from infermap.engine import MapEngine
from infermap.config import from_config
from infermap.scorers import default_scorers, scorer
from infermap.providers import extract_schema


def map(source, target, **kwargs) -> MapResult:
    """Convenience function: create a MapEngine and map source to target.

    Parameters
    ----------
    source:
        Source data (CSV path, DataFrame, DB URI, schema YAML, …).
    target:
        Target data — same variety of inputs.
    **kwargs:
        Forwarded to ``MapEngine.map()``.

    Returns
    -------
    MapResult
    """
    engine = MapEngine()
    return engine.map(source, target, **kwargs)


__all__ = [
    "FieldInfo",
    "FieldMapping",
    "MapResult",
    "SchemaInfo",
    "ScorerResult",
    "ApplyError",
    "ConfigError",
    "InferMapError",
    "MapEngine",
    "from_config",
    "default_scorers",
    "scorer",
    "extract_schema",
    "map",
]
