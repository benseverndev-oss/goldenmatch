"""GoldenPipe -- pluggable pipeline framework for data quality."""
__version__ = "1.0.2"

from goldenpipe._api import run, run_df, run_stages
from goldenpipe.pipeline import Pipeline
from goldenpipe.models.context import (
    PipeContext, PipeResult, StageResult, Decision,
    StageStatus, PipeStatus,
)
from goldenpipe.models.stage import StageInfo, Stage, stage
from goldenpipe.models.config import StageSpec, PipelineConfig
from goldenpipe.config.loader import load_config
from goldenpipe.decisions import severity_gate, pii_router, row_count_gate

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
