"""GoldenPipe -- pluggable pipeline framework for data quality."""
__version__ = "1.2.1"

from goldenpipe._api import run, run_df, run_stages
from goldenpipe.config.loader import load_config
from goldenpipe.decisions import pii_router, row_count_gate, severity_gate
from goldenpipe.models.config import PipelineConfig, StageSpec
from goldenpipe.models.context import (
    Decision,
    PipeContext,
    PipeResult,
    PipeStatus,
    StageResult,
    StageStatus,
)
from goldenpipe.models.stage import Stage, StageInfo, stage
from goldenpipe.pipeline import Pipeline

__all__ = [
    "run", "run_df", "run_stages",
    "Pipeline",
    "PipeContext", "PipeResult", "StageResult", "Decision",
    "StageStatus", "PipeStatus",
    "StageInfo", "Stage", "stage",
    "StageSpec", "PipelineConfig",
    "load_config",
    "severity_gate", "pii_router", "row_count_gate",
    "__version__",
]
