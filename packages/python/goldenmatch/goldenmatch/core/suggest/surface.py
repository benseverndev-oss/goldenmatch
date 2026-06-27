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


def heal(df, config, *, step_cap: int = _HEAL_STEP_CAP) -> HealOutcome:
    """Bounded verified heal loop: re-run -> verified suggest -> apply top -> repeat
    until the healer goes quiet (or step_cap, or a repeated patch id). Returns the
    healed config, the applied trail (auditable), and the last DedupeResult."""
    from goldenmatch._api import dedupe_df  # deferred
    from goldenmatch.core.suggest.adapter import suggest_from_result  # deferred
    from goldenmatch.core.suggest.apply import apply_suggestion  # deferred

    trail = []
    applied_ids = set()
    last_result = None
    for _ in range(step_cap):
        last_result = dedupe_df(df, config=config)
        sugs = suggest_from_result(last_result, df, verify=True)
        if not sugs:
            break
        top = sugs[0]
        if top.id in applied_ids:
            break
        applied_ids.add(top.id)
        config = apply_suggestion(config, top)
        trail.append(top)
    return HealOutcome(config=config, trail=trail, result=last_result)
