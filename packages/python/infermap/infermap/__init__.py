"""infermap — inference-driven schema mapping engine."""

__version__ = "0.5.0"

from infermap.config import from_config
from infermap.detect import detect_domain, detect_domain_detailed
from infermap.domain_pack import DomainPackTarget
from infermap.engine import MapEngine
from infermap.errors import ApplyError, ConfigError, InferMapError

# Identity Graph bridge (optional; lazy-imports goldenmatch on use). Exposed
# at the top level so callers write `infermap.write_aliases_from_mapping(...)`
# without reaching into a submodule. The helper itself raises ImportError
# with a clear remediation message if goldenmatch isn't installed.
from infermap.identity import AliasWriteResult, write_aliases_from_mapping
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
    "DomainPackTarget",
    "detect_domain",
    "detect_domain_detailed",
    "MapEngine",
    "from_config",
    "default_scorers",
    "scorer",
    "extract_schema",
    "map",
    "AliasWriteResult",
    "write_aliases_from_mapping",
]
