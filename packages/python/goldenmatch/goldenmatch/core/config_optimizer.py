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
  next structured ``ConfigEdit``s (the shared closed vocabulary) from the best
  trial's zero-label ``confidence_reasons``. The live path is env-gated
  (``GOLDENMATCH_AUTOCONFIG_LLM=1`` + ``OPENAI_API_KEY``); it degrades to the grid
  seed when unconfigured.

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
from goldenmatch.core.config_edits import (
    _PERTURBABLE_TYPES,
    BlockingKeyEdit,
    BlockingStrategyEdit,
    ConfigEdit,
    ScorerSwap,
    ThresholdShift,
    WeightShift,
    _clamp,
    _perturbable_matchkeys,
    parse_llm_edits,
)

if TYPE_CHECKING:
    from goldenmatch.core.complexity_profile import ComplexityProfile

logger = logging.getLogger(__name__)

# Global threshold offsets applied to every perturbable matchkey (clamped to
# [0, 1]). 0.0 is the warm-start baseline and is always included.
_DEFAULT_THRESHOLD_OFFSETS: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10)

_LLM_PROPOSER_SYSTEM = (
    "You are an expert in entity-resolution configuration driving an empirical "
    "config search. Each round you see the best config so far, its label-free "
    "confidence score, the diagnostic reasons behind that score, and the trials "
    "already tried. Propose a short list of minimal structured config edits (as "
    "JSON, from the closed vocabulary you are given) most likely to raise the "
    "confidence score, without repeating a trial already tried. Respond with "
    "valid JSON only."
)


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
    """AI-driven iteration: an LLM agent proposes the next lever moves.

    Round 0 seeds with the grid sweep so the agent has trials to reason about;
    each later round reads the best trial + its zero-label ``confidence_reasons``
    and asks the LLM for the next moves as a list of structured ``ConfigEdit``s
    (the same closed vocabulary the deterministic proposers use). The loop scores
    one candidate per valid edit, so the report attributes every move to a lever.

    The live OpenAI path is env-gated (``GOLDENMATCH_AUTOCONFIG_LLM=1`` +
    ``OPENAI_API_KEY``). Inject ``propose_fn`` (returning a list of
    ``ConfigEdit``s) to bypass the network in tests. When neither is available,
    later rounds return nothing → the search degrades to the grid seed.
    """

    single_round = False

    def __init__(
        self,
        *,
        offsets: tuple[float, ...] = _DEFAULT_THRESHOLD_OFFSETS,
        model: str = "gpt-4o-mini",
        max_llm_calls: int = 3,
        propose_fn: Callable[[SearchState], list[ConfigEdit] | None] | None = None,
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
        edits = self._propose_fn(state) if self._propose_fn is not None else self._call_llm(state, best)
        if not edits:
            return []
        self._calls += 1
        out: list[tuple[str, GoldenMatchConfig]] = []
        for edit in edits:
            cfg = edit.apply(best.config)
            if cfg is not None:
                out.append((f"llm-r{state.round}:{edit.label}", cfg))
        return out

    def _call_llm(self, state: SearchState, best: OptimizerTrial) -> list[ConfigEdit]:
        import json
        import os

        if os.environ.get("GOLDENMATCH_AUTOCONFIG_LLM", "0") != "1":
            return []
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return []
        try:
            from openai import (  # pyright: ignore[reportMissingImports]  # optional dep
                OpenAI,
            )
        except ImportError:
            return []

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
            return []
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        return parse_llm_edits(payload)

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
  {{"action": "stop"}}                    -- no edit is likely to help; stop.
  {{"edits": [ ...edit objects... ]}}     -- propose 1-3 minimal edits to try next.

Each edit object is one of (every edit applies to the best config above):
  {{"op": "threshold_shift", "delta": -0.05}}
  {{"op": "scorer_swap", "matchkey": "...", "field": "...", "scorer": "token_sort"}}
  {{"op": "blocking_strategy", "strategy": "multi_pass"}}
  {{"op": "blocking_key", "action": "add", "fields": ["surname"], "transforms": ["soundex"]}}
  {{"op": "weight_shift", "matchkey": "...", "field": "...", "delta": 0.5}}
  {{"op": "matchkey_type", "matchkey": "...", "target_type": "probabilistic"}}

Do not repeat a trial already tried. Respond with valid JSON only.
"""


class CoordinateDescentProposer:
    """Deterministic multi-lever search. Each round optimizes ONE lever family
    off the best config so far: thresholds, per-field scorer, per-field weight
    (multi-field weighted matchkeys only), blocking strategy, then candidate
    blocking keys. Cheaper than a full cross-product and fully CI-testable (no LLM).

    Tracks its own family pointer (not ``state.round``) so empty families are
    skipped; returns ``[]`` only once all families are exhausted.

    ``blocking_key_adds`` defaults to empty (the optimizer doesn't guess columns);
    pass candidate field-sets to let the search recover true matches that the base
    blocking key never co-locates — a recall gain no threshold move can reach.
    """

    single_round = False
    _FAMILIES = ("threshold", "scorer", "weight", "blocking", "blocking_key")

    def __init__(
        self,
        *,
        offsets: tuple[float, ...] = _DEFAULT_THRESHOLD_OFFSETS,
        scorers: tuple[str, ...] = (
            # #491: levenshtein + soundex_match were unreachable from heuristic
            # auto-config; the optimizer's scorer family is the empirical home for
            # the data-dependent string-similarity choice. (dice/jaccard are
            # bloom-filter/PPRL scorers — they expect hex CLKs, not plain text —
            # so they are NOT in the general candidate set.)
            "token_sort", "ensemble", "levenshtein", "soundex_match",
        ),
        weight_deltas: tuple[float, ...] = (-0.5, 0.5),
        blocking_strategies: tuple[str, ...] = ("multi_pass",),
        blocking_key_adds: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        self._offsets = offsets
        self._scorers = scorers
        self._weight_deltas = weight_deltas
        self._blocking_strategies = blocking_strategies
        self._blocking_key_adds = blocking_key_adds
        self._fam_idx = 0

    def _edits(self, family: str, base: GoldenMatchConfig) -> list[ConfigEdit]:
        if family == "threshold":
            return [ThresholdShift(o) for o in self._offsets]
        if family == "scorer":
            scorer_edits: list[ConfigEdit] = []
            for mk in _perturbable_matchkeys(base):
                for f in (mk.fields or []):
                    for sc in self._scorers:
                        if f.scorer != sc:
                            scorer_edits.append(ScorerSwap(mk.name, f.field, sc))
            return scorer_edits
        if family == "weight":
            # Single-field weighted matchkeys normalize the weight away, so a
            # shift is a no-op there — only emit for multi-field matchkeys.
            weight_edits: list[ConfigEdit] = []
            for mk in base.get_matchkeys():
                fields = mk.fields or []
                if getattr(mk, "type", None) != "weighted" or len(fields) < 2:
                    continue
                for f in fields:
                    for d in self._weight_deltas:
                        weight_edits.append(WeightShift(mk.name, f.field, d))
            return weight_edits
        if family == "blocking":
            return [BlockingStrategyEdit(s) for s in self._blocking_strategies]
        if family == "blocking_key":
            return [BlockingKeyEdit("add", fields) for fields in self._blocking_key_adds]
        return []

    def propose(self, state: SearchState) -> list[tuple[str, GoldenMatchConfig]]:
        base = state.best.config if state.best is not None else state.base_config
        while self._fam_idx < len(self._FAMILIES):
            family = self._FAMILIES[self._fam_idx]
            self._fam_idx += 1
            out: list[tuple[str, GoldenMatchConfig]] = []
            for edit in self._edits(family, base):
                cfg = edit.apply(base)
                if cfg is not None:
                    out.append((edit.label, cfg))
            if out:
                return out
        return []


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
        if key in ("coordinate", "coordinate_descent"):
            return CoordinateDescentProposer(offsets=offsets)
        if key == "llm":
            return LLMProposer(offsets=offsets, model=model, max_llm_calls=max_llm_calls)
        raise ValueError(
            f"unknown proposer {proposer!r}; use 'grid', 'coordinate', 'llm', "
            "or a Proposer instance"
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
    max_rounds: int = 6,
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
        if not candidates:  # proposer signals it is done
            break
        for label, cfg in candidates:
            fp = _fingerprint(cfg)
            if fp in seen:
                continue
            seen.add(fp)
            state.trials.append(_score_candidate(
                label, cfg, objective,
                controller=controller, sample=sample, df=df, ground_truth=ground_truth,
            ))
            if max_trials is not None and len(state.trials) >= max_trials:
                break
        state.round += 1
        if max_trials is not None and len(state.trials) >= max_trials:
            break
        if getattr(prop, "single_round", False):
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
