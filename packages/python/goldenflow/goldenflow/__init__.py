__version__ = "1.1.0"

from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    MappingSpec,
    SplitSpec,
    TransformSpec,
)
from goldenflow.engine.transformer import TransformEngine, TransformResult

# Engine
from goldenflow.engine.manifest import Manifest, TransformRecord, TransformError
from goldenflow.engine.profiler_bridge import DatasetProfile, ColumnProfile
from goldenflow.engine.selector import select_transforms
from goldenflow.engine.differ import diff_dataframes, DiffResult

# Transforms
from goldenflow.transforms import (
    TransformInfo,
    register_transform,
    get_transform,
    list_transforms,
    parse_transform_name,
)

# Mapping
from goldenflow.mapping.schema_mapper import SchemaMapper, ColumnMapping

# Config
from goldenflow.config.loader import load_config, save_config, merge_configs
from goldenflow.config.learner import learn_config

# Domains
from goldenflow.domains.base import DomainPack
from goldenflow.domains import load_domain

# Connectors
from goldenflow.connectors.file import read_file, write_file

# Import transform modules so they register with the registry
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.names  # noqa: F401
import goldenflow.transforms.address  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.numeric  # noqa: F401
import goldenflow.transforms.auto_correct  # noqa: F401
import goldenflow.transforms.email  # noqa: F401
import goldenflow.transforms.identifiers  # noqa: F401
import goldenflow.transforms.url  # noqa: F401
import goldenflow.notebook  # noqa: F401 — register Jupyter _repr_html_ methods


def transform_file(path, config=None, output_dir=None):
    """Convenience function: transform a file."""
    from pathlib import Path
    engine = TransformEngine(config=config)
    return engine.transform_file(Path(path), output_dir=Path(output_dir) if output_dir else None)


def transform_df(df, config=None):
    """Convenience function: transform a DataFrame."""
    engine = TransformEngine(config=config)
    return engine.transform_df(df)


__all__ = [
    # Core engine
    "TransformEngine",
    "TransformResult",
    # Config schema
    "GoldenFlowConfig",
    "TransformSpec",
    "SplitSpec",
    "FilterSpec",
    "DedupSpec",
    "MappingSpec",
    # Convenience functions
    "transform_file",
    "transform_df",
    # Engine — manifest
    "Manifest",
    "TransformRecord",
    "TransformError",
    # Engine — profiler
    "DatasetProfile",
    "ColumnProfile",
    # Engine — selector
    "select_transforms",
    # Engine — differ
    "diff_dataframes",
    "DiffResult",
    # Transforms registry
    "TransformInfo",
    "register_transform",
    "get_transform",
    "list_transforms",
    "parse_transform_name",
    # Mapping
    "SchemaMapper",
    "ColumnMapping",
    # Config
    "load_config",
    "save_config",
    "merge_configs",
    "learn_config",
    # Domains
    "DomainPack",
    "load_domain",
    # Connectors
    "read_file",
    "write_file",
]
