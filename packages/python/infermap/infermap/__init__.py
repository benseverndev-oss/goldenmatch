"""infermap — inference-driven schema mapping engine."""

__version__ = "0.4.0"

from infermap.config import from_config
from infermap.detect import detect_domain, detect_domain_detailed
from infermap.domain_pack import DomainPackTarget
from infermap.engine import MapEngine
from infermap.errors import ApplyError, ConfigError, InferMapError
from infermap.providers import extract_schema
from infermap.scorers import default_scorers, scorer
from infermap.types import FieldInfo, FieldMapping, MapResult, SchemaInfo, ScorerResult


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
