"""GoldenPipe -- Golden Suite orchestrator.

Usage:
    import goldenpipe as gp
    result = gp.run("customers.csv")
"""
__version__ = "0.1.0"

from goldenpipe.pipeline import run, Pipeline, PipeResult
from goldenpipe.decisions import decide_flow, decide_match, FlowDecision, MatchDecision

__all__ = [
    "run", "Pipeline", "PipeResult",
    "decide_flow", "decide_match", "FlowDecision", "MatchDecision",
]
