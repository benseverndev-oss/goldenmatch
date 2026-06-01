__version__ = "1.2.0"

import goldenflow.notebook  # noqa: F401 — register Jupyter _repr_html_ methods
import goldenflow.transforms.address  # noqa: F401
import goldenflow.transforms.auto_correct  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.email  # noqa: F401
import goldenflow.transforms.identifiers  # noqa: F401
import goldenflow.transforms.names  # noqa: F401
import goldenflow.transforms.numeric  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401

# Import transform modules so they register with the registry
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.url  # noqa: F401
from goldenflow.config.learner import learn_config

# Config
from goldenflow.config.loader import load_config, merge_configs, save_config
from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    MappingSpec,
    SplitSpec,
    TransformSpec,
)

# Connectors
from goldenflow.connectors.file import read_file, write_file
from goldenflow.domains import load_domain

# Domains
from goldenflow.domains.base import DomainPack
from goldenflow.engine.differ import DiffResult, diff_dataframes

# Engine
from goldenflow.engine.manifest import Manifest, TransformError, TransformRecord
from goldenflow.engine.profiler_bridge import ColumnProfile, DatasetProfile
from goldenflow.engine.selector import select_transforms
from goldenflow.engine.transformer import TransformEngine, TransformResult

# Mapping
from goldenflow.mapping.schema_mapper import ColumnMapping, SchemaMapper

# Transforms
from goldenflow.transforms import (
    TransformInfo,
    get_transform,
    list_transforms,
    parse_transform_name,
    register_transform,
)


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
