"""Sketch-then-verify throughput tier (#1083): banding, sketch-distance verify,
and the honest LSH-theoretic posture. Isolated from the accuracy scorer."""
from __future__ import annotations

import os
from goldenmatch.config.schemas import ThroughputConfig


class ThroughputNotApplicableError(Exception):
    """Raised when the throughput tier is requested but the data has no text
    column to sketch on. Explicit refuse - no silent fall-back to the accuracy
    tier (mirrors ControllerNotConfidentError)."""


def resolve_throughput_config(arg=None, config=None) -> ThroughputConfig | None:
    """Resolve throughput posture: kwarg -> env -> off.

    ``arg`` accepts True (enable w/ defaults), a float (recall_target), a
    ThroughputConfig, or None. If None and config.throughput is set, that wins.
    Env: GOLDENMATCH_THROUGHPUT (truthy), GOLDENMATCH_THROUGHPUT_RECALL,
    GOLDENMATCH_THROUGHPUT_SIMILARITY.
    """
    if isinstance(arg, ThroughputConfig):
        return arg
    if arg is True:
        return ThroughputConfig(enabled=True)
    if isinstance(arg, (int, float)) and not isinstance(arg, bool):
        return ThroughputConfig(enabled=True, recall_target=float(arg))
    if arg is False:
        return None
    if config is not None and getattr(config, "throughput", None) is not None:
        return config.throughput
    env = os.environ.get("GOLDENMATCH_THROUGHPUT")
    if env and env.strip().lower() in ("1", "true", "yes", "on"):
        recall = os.environ.get("GOLDENMATCH_THROUGHPUT_RECALL")
        sim = os.environ.get("GOLDENMATCH_THROUGHPUT_SIMILARITY")
        return ThroughputConfig(
            enabled=True,
            recall_target=float(recall) if recall else 0.95,
            similarity_threshold=float(sim) if sim else None,
        )
    return None
