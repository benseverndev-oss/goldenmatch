__version__ = "0.1.0"

from goldenflow.config.schema import (
    DedupSpec,
    FilterSpec,
    GoldenFlowConfig,
    MappingSpec,
    SplitSpec,
    TransformSpec,
)
from goldenflow.engine.transformer import TransformEngine, TransformResult

# Import transform modules so they register with the registry
import goldenflow.transforms.text  # noqa: F401
import goldenflow.transforms.phone  # noqa: F401
import goldenflow.transforms.names  # noqa: F401
import goldenflow.transforms.address  # noqa: F401
import goldenflow.transforms.dates  # noqa: F401
import goldenflow.transforms.categorical  # noqa: F401
import goldenflow.transforms.numeric  # noqa: F401
import goldenflow.transforms.auto_correct  # noqa: F401


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
    "TransformEngine",
    "TransformResult",
    "GoldenFlowConfig",
    "TransformSpec",
    "SplitSpec",
    "FilterSpec",
    "DedupSpec",
    "MappingSpec",
    "transform_file",
    "transform_df",
]
