"""Shared orchestration for the config-suggestion (healer) surfaces: the free
headroom trigger, the cheap/opt-in suggest gate, the heal loop, and the wire
serializer. See docs/superpowers/specs/2026-06-26-healer-default-pipeline-design.md.

This module only contains the FREE headroom trigger today; the cheap/opt-in
suggest gate, heal loop, and wire serializer land in later tasks. The trigger
is intentionally dependency-light: no native kernel, no pipeline re-run, no
heavy imports at module top.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from goldenmatch.core.complexity_profile import HealthVerdict

if TYPE_CHECKING:
    from goldenmatch._api import DedupeResult
    from goldenmatch.config.schemas import GoldenMatchConfig

# A score-histogram dip is "present" when the committed profile's Hartigan-style
# dip statistic clears this floor. The controller's own RED floor is 0.005
# (``ScoringProfile.health``) and its normalized-signal vector treats 0.1 as a
# full dip; 0.05 sits well above the RED floor and at half the normalization
# ceiling, so it flags genuine bimodality (a lower-threshold sweet spot) without
# tripping on near-unimodal noise.
_DIP_THRESHOLD = 0.05


@dataclass(frozen=True)
class HeadroomReason:
    """Why the committed auto-config run has headroom to improve.

    ``kind`` is a small human/machine-readable tag, e.g. ``"health:RED"``,
    ``"health:YELLOW"``, or ``"dip"``.
    """

    kind: str


def headroom_signal(result) -> HeadroomReason | None:
    """Return a reason when the committed auto-config run shows headroom.

    PURE and FREE: reads only ``result.postflight_report.controller_history``
    (already computed by the controller). No native kernel, no pipeline re-run.

    Fires when the committed ``ComplexityProfile`` is RED/YELLOW, or when an
    otherwise-GREEN config carries a score-histogram dip (a lower-threshold
    sweet spot). Returns ``None`` when the controller never ran (explicit-config
    path → ``controller_history is None``), when there is no committed entry, or
    when anything is missing/raises. Never raises.
    """
    try:
        report = getattr(result, "postflight_report", None)
        history = getattr(report, "controller_history", None) if report is not None else None
        if history is None:
            return None
        committed = history.pick_committed()
        if committed is None:
            return None
        profile = getattr(committed, "profile", None)
        if profile is None:
            return None

        health = profile.health()
        if health in (HealthVerdict.RED, HealthVerdict.YELLOW):
            return HeadroomReason(kind=f"health:{health.name}")

        # Otherwise-GREEN: a meaningful dip means a threshold sweet spot exists.
        if profile.scoring.dip_statistic >= _DIP_THRESHOLD:
            return HeadroomReason(kind="dip")

        return None
    except Exception:
        return None


def maybe_suggest(result, df, *, verify: bool = False):
    """Default-path gate: returns [] (without calling the kernel) unless the free
    headroom trigger fires and the kill-switch is off. Delegates to the artifacts-in
    suggest_from_result. Graceful []-on-no-native is handled downstream."""
    if os.environ.get("GOLDENMATCH_SUGGEST_ON_DEDUPE", "1").strip() == "0":
        return []
    if headroom_signal(result) is None:
        return []
    from goldenmatch.core.suggest.adapter import suggest_from_result  # deferred
    return suggest_from_result(result, df, verify=verify)


def serialize_suggestions(suggestions, *, verified: bool) -> list[dict]:
    """The single wire shape every surface emits. `verified` is caller-supplied
    (Suggestion has no such field): default/maybe_suggest pass False; suggest=/heal=
    pass True."""
    return [{"id": s.id, "kind": s.kind, "target": s.target,
             "rationale": s.rationale, "verified": verified,
             "patch": dict(s.patch)} for s in suggestions]


@dataclass
class HealOutcome:
    """The result of a bounded heal loop: the healed config, the auditable trail
    of applied suggestions (in order), and the last DedupeResult."""

    config: GoldenMatchConfig
    trail: list                       # list[Suggestion], applied in order
    result: DedupeResult | None       # the last DedupeResult (None if no step ran)


_HEAL_STEP_CAP = 5


def _resolve_step_cap(step_cap: int | None) -> int:
    """Resolve the heal iteration cap (#1404): explicit kwarg → env
    ``GOLDENMATCH_HEAL_STEP_CAP`` → the ``_HEAL_STEP_CAP`` default. Each step is a
    full dedupe plus up to ``max_verify`` verification re-runs, so this is the
    coarsest cost lever. Clamped to >= 1; invalid env falls back to the default."""
    if step_cap is None:
        raw = os.environ.get("GOLDENMATCH_HEAL_STEP_CAP", "").strip()
        if not raw:
            return _HEAL_STEP_CAP
        try:
            step_cap = int(raw)
        except ValueError:
            return _HEAL_STEP_CAP
    return max(1, step_cap)


def _resolve_min_health_gain(min_health_gain: float | None) -> float | None:
    """Resolve the marginal-gain early-stop threshold (#1404): explicit kwarg →
    env ``GOLDENMATCH_HEAL_MIN_HEALTH_GAIN`` → ``None`` (disabled, the default).

    When a float is resolved, the heal loop stops once an iteration's cluster
    health fails to improve on the previous iteration's by at least this much --
    i.e. the healer stops when it is no longer helping, instead of always
    exhausting ``step_cap``. Left off by default so the shipped convergence is
    byte-identical; a value of ``0.0`` stops on the first non-improving step."""
    if min_health_gain is not None:
        return min_health_gain
    raw = os.environ.get("GOLDENMATCH_HEAL_MIN_HEALTH_GAIN", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _safe_cluster_health(result, df) -> float | None:
    """Cluster-health proxy for the heal early-stop, fail-open. Returns ``None``
    (never triggers a stop) when the result has no clusters or anything raises --
    so callers/tests without a real DedupeResult keep the default convergence."""
    try:
        clusters = getattr(result, "clusters", None)
        if not clusters:
            return None
        from goldenmatch.core.suggest.health import suggestion_health_from_clusters
        return suggestion_health_from_clusters(clusters, df.height)
    except Exception:
        return None


def heal(
    df,
    config,
    *,
    step_cap: int | None = None,
    max_verify: int | None = None,
    min_health_gain: float | None = None,
) -> HealOutcome:
    """Bounded verified heal loop: re-run -> verified suggest -> apply top -> repeat
    until the healer goes quiet (or step_cap, or a repeated patch id). Returns the
    healed config, the applied trail (auditable), and the last DedupeResult.

    Cost bounds (#1404):
    - ``step_cap`` (env ``GOLDENMATCH_HEAL_STEP_CAP``) caps the outer iterations.
    - ``max_verify`` (env ``GOLDENMATCH_SUGGEST_MAX_VERIFY``) caps the per-step
      verification fan-out. The healer applies only the TOP surviving suggestion,
      so ``max_verify=1`` (verify just the top candidate) is the cheapest mode.
    - ``min_health_gain`` (env ``GOLDENMATCH_HEAL_MIN_HEALTH_GAIN``) early-stops
      when marginal cluster-health gain flattens; ``None`` (default) disables it.
    - The expensive goldencheck variant scan is memoized for the whole loop via
      ``variant_risk_cache()`` (runs once over the unchanged frame, not per step).
    """
    from goldenmatch._api import dedupe_df  # deferred
    from goldenmatch.core.suggest.adapter import (  # deferred
        suggest_from_result,
        variant_risk_cache,
    )
    from goldenmatch.core.suggest.apply import apply_suggestion  # deferred

    step_cap = _resolve_step_cap(step_cap)
    min_health_gain = _resolve_min_health_gain(min_health_gain)

    trail = []
    applied_ids = set()
    last_result = None
    prev_health: float | None = None
    with variant_risk_cache():
        for _ in range(step_cap):
            last_result = dedupe_df(df, config=config)
            if min_health_gain is not None:
                cur_health = _safe_cluster_health(last_result, df)
                if (
                    cur_health is not None
                    and prev_health is not None
                    and cur_health - prev_health < min_health_gain
                ):
                    break  # marginal cluster-health gain flattened -> stop early
                if cur_health is not None:
                    prev_health = cur_health
            sugs = suggest_from_result(
                last_result, df, verify=True, max_verify=max_verify
            )
            if not sugs:
                break
            top = sugs[0]
            if top.id in applied_ids:
                break
            applied_ids.add(top.id)
            config = apply_suggestion(config, top)
            trail.append(top)
    return HealOutcome(config=config, trail=trail, result=last_result)
