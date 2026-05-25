"""Agentic config optimizer: empirical lever search for auto-config.

Hand it a DataFrame; it warm-starts from :func:`auto_configure_df`, generates a
candidate-config space, scores each candidate empirically, and returns the best
config plus a per-trial report. The default objective is the label-free
zero-label confidence layer (``core/zero_label_confidence.py``) scored on a
sample; when ground-truth pairs are supplied it switches to supervised F1 on the
full frame.

Scope (v1): the search sweeps matchkey thresholds around the warm-start config.
Multi-lever search (scorer choice, blocking strategy, weighted-vs-probabilistic)
is a tracked follow-up — see the design doc
``docs/design/2026-05-25-zero-label-confidence-autoconfig-design.md`` and the
filed optimizer issues. The headline path is: search on a sample by confidence,
pick the best, then the caller reruns the winning config on the full data.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig

if TYPE_CHECKING:
    from goldenmatch.core.complexity_profile import ComplexityProfile

logger = logging.getLogger(__name__)

# Global threshold offsets applied to every perturbable matchkey (clamped to
# [0, 1]). 0.0 is the warm-start baseline and is always included.
_DEFAULT_THRESHOLD_OFFSETS: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10)

_PERTURBABLE_TYPES = ("weighted", "probabilistic")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class OptimizerTrial:
    """One scored candidate config."""

    label: str
    config: GoldenMatchConfig
    objective: str  # "confidence" | "f1"
    score: float
    profile: ComplexityProfile | None = None
    reasons: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class OptimizeResult:
    """Outcome of an :func:`optimize_config` search."""

    best_config: GoldenMatchConfig
    best_trial: OptimizerTrial
    trials: tuple[OptimizerTrial, ...]
    objective: str
    sample_size: int
    baseline_label: str = "baseline"

    def report(self) -> str:
        lines = [
            f"ConfigOptimizer - objective={self.objective}, "
            f"scored_on={self.sample_size} rows, {len(self.trials)} trial(s)",
        ]
        ranked = sorted(self.trials, key=lambda t: t.score, reverse=True)
        for t in ranked:
            marker = "*" if t is self.best_trial else " "
            if t.error:
                lines.append(f" {marker} {t.label:<16} ERROR: {t.error}")
            else:
                detail = f"; {', '.join(t.reasons)}" if t.reasons else ""
                lines.append(f" {marker} {t.label:<16} {self.objective}={t.score:.4f}{detail}")
        return "\n".join(lines)


def _perturbable_matchkeys(config: GoldenMatchConfig) -> list:
    return [
        mk for mk in config.get_matchkeys()
        if getattr(mk, "type", None) in _PERTURBABLE_TYPES and mk.threshold is not None
    ]


def _threshold_variants(
    base: GoldenMatchConfig, offsets: tuple[float, ...]
) -> list[tuple[str, GoldenMatchConfig]]:
    """Deep-copied config variants with every perturbable matchkey threshold
    shifted by each ``offset`` (clamped). Variants whose thresholds collapse to
    the same clamped values are de-duplicated. When nothing is perturbable
    (e.g. an exact-only config), returns just the baseline."""
    if not _perturbable_matchkeys(base):
        return [("baseline", base)]

    variants: list[tuple[str, GoldenMatchConfig]] = []
    seen: set[tuple[float, ...]] = set()
    for off in offsets:
        cfg = base.model_copy(deep=True)
        thresholds: list[float] = []
        for mk in cfg.get_matchkeys():
            if getattr(mk, "type", None) in _PERTURBABLE_TYPES and mk.threshold is not None:
                mk.threshold = _clamp(mk.threshold + off)
                thresholds.append(mk.threshold)
        key = tuple(round(t, 6) for t in thresholds)
        if key in seen:
            continue
        seen.add(key)
        label = "baseline" if off == 0.0 else f"threshold{off:+.2f}"
        variants.append((label, cfg))
    return variants


def _score_confidence(
    controller, sample: pl.DataFrame, config: GoldenMatchConfig
) -> tuple[float, ComplexityProfile, tuple[str, ...]]:
    from goldenmatch.core.profile_emitter import profile_capture

    with profile_capture() as emitter:
        controller._run_pipeline_sample(sample, None, config)
    profile = controller._assemble_profile(
        emitter, df=sample, iteration=0, reference=None, config=config,
    )
    z = profile.zero_label
    if z is None:
        return 0.0, profile, ()
    return z.overall_confidence, profile, z.confidence_reasons


def _score_f1(
    config: GoldenMatchConfig, df: pl.DataFrame, ground_truth: set[tuple]
) -> tuple[float, tuple[str, ...]]:
    from goldenmatch.core.evaluate import evaluate_clusters
    from goldenmatch.core.pipeline import run_dedupe_df

    result = run_dedupe_df(df, config=config)
    ev = evaluate_clusters(result["clusters"], ground_truth)
    reasons = (
        f"P={ev.precision:.3f} R={ev.recall:.3f} "
        f"(tp={ev.tp} fp={ev.fp} fn={ev.fn})",
    )
    return ev.f1, reasons


def optimize_config(
    df: pl.DataFrame | pl.LazyFrame,
    *,
    base_config: GoldenMatchConfig | None = None,
    ground_truth: set[tuple] | None = None,
    objective: str = "auto",
    threshold_offsets: tuple[float, ...] = _DEFAULT_THRESHOLD_OFFSETS,
    sample_size: int | None = None,
    llm_provider: str | None = None,
) -> OptimizeResult:
    """Search the candidate-config space and return the best config + trials.

    Args:
        df: input frame (deduplication mode).
        base_config: warm-start config; when ``None`` the warm start is
            :func:`auto_configure_df` (``confidence_required=False`` so it never
            raises ``ControllerNotConfidentError`` mid-search).
        ground_truth: canonical ``(id_a, id_b)`` pairs over 0-based row indices
            into ``df``. When supplied, the objective defaults to ``"f1"``.
        objective: ``"auto"`` (f1 if ground_truth else confidence),
            ``"confidence"`` (zero-label, scored on a sample), or ``"f1"``
            (supervised, scored on the full frame).
        threshold_offsets: global threshold shifts applied to every perturbable
            matchkey.
        sample_size: override the confidence-objective sample size.
        llm_provider: forwarded to the warm-start ``auto_configure_df``.

    The headline (confidence) path scores on a sample so the search is cheap;
    the caller reruns ``result.best_config`` on the full data.
    """
    if isinstance(df, pl.LazyFrame):
        df = df.collect()
    if not isinstance(df, pl.DataFrame):
        raise TypeError(
            f"optimize_config requires a polars DataFrame or LazyFrame, got {type(df)!r}"
        )

    if objective == "auto":
        objective = "f1" if ground_truth is not None else "confidence"
    if objective not in ("confidence", "f1"):
        raise ValueError(f"objective must be 'confidence', 'f1', or 'auto'; got {objective!r}")
    if objective == "f1" and ground_truth is None:
        raise ValueError("objective='f1' requires ground_truth pairs")

    if base_config is None:
        from goldenmatch.core.autoconfig import auto_configure_df

        base_config = auto_configure_df(
            df, confidence_required=False, llm_provider=llm_provider,
        )

    variants = _threshold_variants(base_config, threshold_offsets)

    controller = None
    sample = df
    if objective == "confidence":
        from goldenmatch.core.autoconfig_controller import (
            AutoConfigController,
            ControllerBudget,
        )
        from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy

        budget = ControllerBudget.for_dataset(df.height)
        if sample_size is not None:
            budget = dataclasses.replace(
                budget, sample_size_default=sample_size, sample_skip_below=0,
            )
        controller = AutoConfigController(
            policy=HeuristicRefitPolicy(), budget=budget, memory=None,
        )
        sample, _ = controller._take_sample(df, reference=None)

    trials: list[OptimizerTrial] = []
    for label, cfg in variants:
        try:
            if objective == "confidence":
                score, profile, reasons = _score_confidence(controller, sample, cfg)
                trials.append(OptimizerTrial(label, cfg, objective, score, profile, reasons))
            else:
                score, reasons = _score_f1(cfg, df, ground_truth)
                trials.append(OptimizerTrial(label, cfg, objective, score, None, reasons))
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not abort the search
            logger.warning("optimizer trial %s failed: %s", label, exc)
            trials.append(
                OptimizerTrial(label, cfg, objective, float("-inf"), None, (), str(exc))
            )

    valid = [t for t in trials if t.error is None]
    if valid:
        # Higher score wins; ties resolve toward the warm-start baseline.
        best_trial = max(valid, key=lambda t: (t.score, t.label == "baseline"))
    else:
        best_trial = trials[0]

    return OptimizeResult(
        best_config=best_trial.config,
        best_trial=best_trial,
        trials=tuple(trials),
        objective=objective,
        sample_size=sample.height if objective == "confidence" else df.height,
    )
