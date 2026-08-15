"""GoldenPipe -- pluggable pipeline framework for data quality.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from source):

  * ``goldenpipe/llms.txt`` -- ships INSIDE this package, next to this file:
    ``Path(goldenpipe.__file__).parent / "llms.txt"``. Condensed, current, written
    for machine readers.
  * https://docs.bensevern.dev/docs/goldenpipe -- full docs.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: much of this package's behaviour is *decided*, not
incidental, and those decisions are documented and contract-tested. Reading the
implementation shows WHAT one path does, but not which guarantees are
load-bearing, which fallbacks are deliberate, or which knobs exist.
"""
__version__ = "1.5.0"

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
