"""Agentic config optimizer: empirical lever search for auto-config.

Hand it a DataFrame; it warm-starts from :func:`auto_configure_df`, drives a
**proposer** to generate candidate configs, scores each candidate empirically,
and returns the best config plus a per-trial report. The default objective is the
label-free zero-label confidence layer (``core/zero_label_confidence.py``) scored
on a sample; when ground-truth pairs are supplied it switches to supervised F1 on
the full frame.

Architecture (design doc ``docs/design/2026-05-25-agentic-config-optimizer-design.md``):
the search is a **proposer / scorer / loop** split so the AI layer is one swappable
piece.

- ``GridProposer`` (default): deterministic threshold sweep — single round,
  byte-identical to the original behavior.
- ``LLMProposer``: the **AI-driven iteration** layer. Round 0 seeds with the grid
  sweep so the agent has trials to reason about; later rounds ask an LLM for the
  next config diff from the best trial's zero-label ``confidence_reasons``. The live
  path is env-gated (``GOLDENMATCH_AUTOCONFIG_LLM=1`` + ``OPENAI_API_KEY``) and reuses
  the controller's ``LLMRefitPolicy`` diff machinery; it degrades to the grid seed
  when unconfigured.

The headline path is: search on a sample by confidence, pick the best, then the
caller reruns the winning config on the full data.
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig

if TYPE_CHECKING:
    from goldenmatch.core.complexity_profile import ComplexityProfile

logger = logging.getLogger(__name__)

# Global threshold offsets applied to every perturbable matchkey (clamped to
# [0, 1]). 0.0 is the warm-start baseline and is always included.
_DEFAULT_THRESHOLD_OFFSETS: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10)

_PERTURBABLE_TYPES = ("weighted", "probabilistic")

_LLM_PROPOSER_SYSTEM = (
    "You are an expert in entity-resolution configuration driving an empirical "
    "config search. Each round you see the best config so far, its label-free "
    "confidence score, the diagnostic reasons behind that score, and the trials "
    "already tried. Propose ONE minimal config diff (as JSON) that is most likely "
    "to raise the confidence score, without repeating a trial already tried. "
    "Respond with valid JSON only."
)


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
    rounds: int = 1
    proposer: str = "grid"

    def report(self) -> str:
        lines = [
            f"ConfigOptimizer - objective={self.objective}, proposer={self.proposer}, "
            f"rounds={self.rounds}, scored_on={self.sample_size} rows, "
            f"{len(self.trials)} trial(s)",
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


@dataclass
class SearchState:
    """Mutable search history handed to the proposer each round."""

    base_config: GoldenMatchConfig
    objective: str
    trials: list[OptimizerTrial] = field(default_factory=list)
    round: int = 0

    @property
    def best(self) -> OptimizerTrial | None:
        valid = [t for t in self.trials if t.error is None]
        if not valid:
            return None
        return max(valid, key=lambda t: (t.score, t.label == "baseline"))


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


# --------------------------------------------------------------------------- #
# Proposers
# --------------------------------------------------------------------------- #

class Proposer(Protocol):
    single_round: bool

    def propose(self, state: SearchState) -> list[tuple[str, GoldenMatchConfig]]: ...


class GridProposer:
    """Deterministic threshold sweep. Single round → byte-identical to the
    original optimizer behavior."""

    single_round = True

    def __init__(self, offsets: tuple[float, ...] = _DEFAULT_THRESHOLD_OFFSETS) -> None:
        self._offsets = offsets

    def propose(self, state: SearchState) -> list[tuple[str, GoldenMatchConfig]]:
        if state.round > 0:
            return []
        return _threshold_variants(state.base_config, self._offsets)


class LLMProposer:
    """AI-driven iteration: an LLM agent proposes the next lever move.

    Round 0 seeds with the grid sweep so the agent has trials to reason about;
    each later round reads the best trial + its zero-label ``confidence_reasons``
    and asks the LLM for the next config diff. Reuses ``apply_config_diff`` from
    ``autoconfig_policy`` so the controller's repair loop and this search speak one
    diff language.

    The live OpenAI path is env-gated (``GOLDENMATCH_AUTOCONFIG_LLM=1`` +
    ``OPENAI_API_KEY``). Inject ``propose_fn`` to bypass the network (tests). When
    neither is available, later rounds return nothing → the search degrades to the
    grid seed.
    """

    single_round = False

    def __init__(
        self,
        *,
        offsets: tuple[float, ...] = _DEFAULT_THRESHOLD_OFFSETS,
        model: str = "gpt-4o-mini",
        max_llm_calls: int = 3,
        propose_fn: Callable[[SearchState], dict | None] | None = None,
    ) -> None:
        self._grid = GridProposer(offsets)
        self._model = model
        self._max_llm_calls = max_llm_calls
        self._propose_fn = propose_fn
        self._calls = 0

    def propose(self, state: SearchState) -> list[tuple[str, GoldenMatchConfig]]:
        if state.round == 0:
            return self._grid.propose(state)
        best = state.best
        if best is None or self._calls >= self._max_llm_calls:
            return []
        diff = self._propose_fn(state) if self._propose_fn is not None else self._call_llm(state, best)
        if not diff:
            return []
        from goldenmatch.core.autoconfig_policy import apply_config_diff

        cfg = apply_config_diff(best.config, diff)
        if cfg is None:
            return []
        self._calls += 1
        return [(f"llm-r{state.round}", cfg)]

    def _call_llm(self, state: SearchState, best: OptimizerTrial) -> dict | None:
        import json
        import os

        if os.environ.get("GOLDENMATCH_AUTOCONFIG_LLM", "0") != "1":
            return None
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            from openai import (  # pyright: ignore[reportMissingImports]  # optional dep
                OpenAI,
            )
        except ImportError:
            return None

        try:
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _LLM_PROPOSER_SYSTEM},
                    {"role": "user", "content": self._build_prompt(state, best)},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1500,
            )
            text = response.choices[0].message.content
        except Exception as exc:  # noqa: BLE001 - LLM failure must not abort the search
            logger.info("LLMProposer: LLM call failed (%s); ending search", exc)
            return None
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if payload.get("action") != "modify":
            return None
        return payload.get("diff") or None

    def _build_prompt(self, state: SearchState, best: OptimizerTrial) -> str:
        tried = "\n".join(
            f"  - {t.label}: {state.objective}={t.score:.4f}"
            for t in state.trials if t.error is None
        ) or "  (none)"
        reasons = "\n".join(f"  - {r}" for r in best.reasons) or "  (none)"
        return f"""\
The empirical config search has run {state.round} round(s). The best config so far
scores {state.objective}={best.score:.4f} (label "{best.label}").

## Diagnostic reasons for the best config
{reasons}

## Trials tried so far
{tried}

## Best config
{best.config.model_dump_json(indent=2)}

## Task
Return JSON with one of:
  {{"action": "stop"}}                  -- no diff is likely to help; stop.
  {{"action": "modify", "diff": {{...}}}} -- propose ONE minimal diff.

The diff is a partial GoldenMatchConfig. Supported keys:
  - "matchkeys": [{{"name": "...", "threshold": 0.7}}]   (threshold change by name)
  - "blocking": {{"keys": [{{"fields": ["surname"], "transforms": ["soundex"]}}]}}
  - "drop_matchkeys": ["name"]

Do not repeat a trial already tried. Respond with valid JSON only.
"""


def _resolve_proposer(
    proposer: str | Proposer,
    offsets: tuple[float, ...],
    model: str,
    max_llm_calls: int,
) -> Proposer:
    if isinstance(proposer, str):
        key = proposer.lower()
        if key == "grid":
            return GridProposer(offsets)
        if key == "llm":
            return LLMProposer(offsets=offsets, model=model, max_llm_calls=max_llm_calls)
        raise ValueError(
            f"unknown proposer {proposer!r}; use 'grid', 'llm', or a Proposer instance"
        )
    return proposer


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

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


def _score_candidate(
    label: str,
    cfg: GoldenMatchConfig,
    objective: str,
    *,
    controller,
    sample: pl.DataFrame,
    df: pl.DataFrame,
    ground_truth: set[tuple] | None,
) -> OptimizerTrial:
    try:
        if objective == "confidence":
            score, profile, reasons = _score_confidence(controller, sample, cfg)
            return OptimizerTrial(label, cfg, objective, score, profile, reasons)
        score, reasons = _score_f1(cfg, df, ground_truth)
        return OptimizerTrial(label, cfg, objective, score, None, reasons)
    except Exception as exc:  # noqa: BLE001 - one bad candidate must not abort the search
        logger.warning("optimizer trial %s failed: %s", label, exc)
        return OptimizerTrial(label, cfg, objective, float("-inf"), None, (), str(exc))


def _fingerprint(cfg: GoldenMatchConfig) -> str:
    try:
        return cfg.model_dump_json()
    except Exception:  # noqa: BLE001
        return repr(cfg)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def optimize_config(
    df: pl.DataFrame | pl.LazyFrame,
    *,
    base_config: GoldenMatchConfig | None = None,
    ground_truth: set[tuple] | None = None,
    objective: str = "auto",
    proposer: str | Proposer = "grid",
    threshold_offsets: tuple[float, ...] = _DEFAULT_THRESHOLD_OFFSETS,
    sample_size: int | None = None,
    max_rounds: int = 4,
    max_trials: int | None = None,
    llm_model: str = "gpt-4o-mini",
    max_llm_calls: int = 3,
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
            ``"confidence"`` (zero-label, scored on a sample), or ``"f1"``.
        proposer: ``"grid"`` (deterministic threshold sweep, default), ``"llm"``
            (AI-driven iteration; env-gated), or a ``Proposer`` instance.
        threshold_offsets: global threshold shifts the grid proposer applies.
        sample_size: override the confidence-objective sample size.
        max_rounds: max proposer rounds (ignored for single-round proposers).
        max_trials: optional hard cap on total scored candidates.
        llm_model / max_llm_calls: LLM proposer tuning.
        llm_provider: forwarded to the warm-start ``auto_configure_df``.

    The confidence path scores on a sample so the search is cheap; the caller
    reruns ``result.best_config`` on the full data.
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

    prop = _resolve_proposer(proposer, threshold_offsets, llm_model, max_llm_calls)
    proposer_name = proposer if isinstance(proposer, str) else type(prop).__name__

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

    state = SearchState(base_config=base_config, objective=objective)
    seen: set[str] = set()
    while state.round < max_rounds:
        candidates = prop.propose(state)
        if not candidates:
            break
        added = 0
        for label, cfg in candidates:
            fp = _fingerprint(cfg)
            if fp in seen:
                continue
            seen.add(fp)
            state.trials.append(_score_candidate(
                label, cfg, objective,
                controller=controller, sample=sample, df=df, ground_truth=ground_truth,
            ))
            added += 1
            if max_trials is not None and len(state.trials) >= max_trials:
                break
        state.round += 1
        if max_trials is not None and len(state.trials) >= max_trials:
            break
        if getattr(prop, "single_round", False) or added == 0:
            break

    valid = [t for t in state.trials if t.error is None]
    if valid:
        # Higher score wins; ties resolve toward the warm-start baseline.
        best_trial = max(valid, key=lambda t: (t.score, t.label == "baseline"))
    else:
        best_trial = state.trials[0]

    return OptimizeResult(
        best_config=best_trial.config,
        best_trial=best_trial,
        trials=tuple(state.trials),
        objective=objective,
        sample_size=sample.height if objective == "confidence" else df.height,
        rounds=state.round,
        proposer=proposer_name,
    )
