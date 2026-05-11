"""RefitPolicy protocol + HeuristicRefitPolicy dispatcher.

The actual rule list (5 rules) lives in ``autoconfig_rules.py`` and is loaded
lazily so this module can be tested in isolation. Rules are pure functions:

    Rule = Callable[
        [ComplexityProfile, GoldenMatchConfig, RunHistory],
        Optional[tuple[GoldenMatchConfig, PolicyDecision]],
    ]

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §HeuristicRefitPolicy rule table (v1).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from goldenmatch.core.autoconfig_controller import IndicatorContext

from goldenmatch.core.autoconfig_history import PolicyDecision, RunHistory
from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict

logger = logging.getLogger(__name__)

# Rule type alias. Returns (new_config, decision) tuple if it fires, else None.
Rule = Callable[
    [ComplexityProfile, Any, RunHistory],
    "tuple[Any, PolicyDecision] | None",
]


class RefitPolicy(Protocol):
    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: IndicatorContext | None = None,
    ) -> Any | None: ...


class HeuristicRefitPolicy:
    """Ordered rule table. First rule that returns non-None wins.

    Per spec §RefitPolicy.propose return semantics (S1-A):
      - None = satisfied → controller breaks loop with POLICY_SATISFIED
      - A new config that == current is also treated as satisfied (bug guard)
      - A previously-seen config is allowed; oscillation handled by controller
    """

    def __init__(self, rules: list[Rule] | None = None) -> None:
        if rules is None:
            from goldenmatch.core.autoconfig_rules import DEFAULT_RULES
            rules = DEFAULT_RULES
        self._rules: list[Rule] = rules

    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: IndicatorContext | None = None,
    ) -> Any | None:
        if profile.health() == HealthVerdict.GREEN:
            return None
        for rule in self._rules:
            outcome = self._call_rule(rule, profile, current, history, ctx)
            if outcome is None:
                continue
            new_config, decision = outcome
            if new_config == current:
                # Bug guard: rule "decided to do nothing" without saying so.
                # Logged at WARN by the controller (not here — keep policy pure).
                return None
            # Attach decision to the latest history entry, if any
            if history.entries:
                history.entries[-1].decision = decision
            return new_config
        # No rule fired → satisfied
        return None

    @staticmethod
    def _call_rule(rule, profile, current, history, ctx):
        """Call a rule with ctx if its signature accepts it; else 3-arg."""
        import inspect
        params = inspect.signature(rule).parameters
        if "ctx" in params:
            return rule(profile, current, history, ctx=ctx)
        return rule(profile, current, history)


class LLMRefitPolicy:
    """Last-resort policy: wraps a base policy (typically HeuristicRefitPolicy);
    when the base returns None AND the profile is still RED/YELLOW, calls an
    LLM to propose a config diff. Falls back to the base's None on any LLM
    error (no API key, network, invalid response).

    Spec §Tier 3 (option B) — heuristic-first, LLM-fallback.
    """

    def __init__(
        self,
        base: RefitPolicy | None = None,
        *,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        max_calls_per_run: int = 5,
        budget: Any | None = None,
    ) -> None:
        self._base = base or HeuristicRefitPolicy()
        self._provider = provider
        self._model = model
        self._max_calls_per_run = max_calls_per_run
        self._budget = budget
        self._calls_this_run = 0  # naive counter; reset by run() in test setups

    def propose(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
        ctx: IndicatorContext | None = None,
    ) -> Any | None:
        # Try the base first (heuristic rules); forward ctx
        base_result = self._base.propose(profile, current, history, ctx=ctx)
        if base_result is not None:
            return base_result

        # Heuristic gave up — but is the profile actually green?
        if profile.health() == HealthVerdict.GREEN:
            return None  # base was satisfied; nothing to do

        # Budget gate
        if self._calls_this_run >= self._max_calls_per_run:
            return None

        try:
            new_config = self._call_llm(profile, current, history)
        except Exception as exc:  # noqa: BLE001
            # Any LLM error → silent fallback to base's None
            logger.info("LLMRefitPolicy: LLM call failed (%s); falling back", exc)
            return None

        self._calls_this_run += 1

        if new_config is None:
            return None
        if new_config == current:
            return None  # LLM "satisfied"

        # Attach decision to the latest history entry, mirroring HeuristicRefitPolicy
        if history.entries:
            history.entries[-1].decision = PolicyDecision(
                rule_name="llm_proposal",
                rationale="LLM-proposed config diff (heuristic exhausted)",
                config_diff={"_": "llm-driven"},  # opaque; full config in entry.config
            )
        return new_config

    def _call_llm(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
    ) -> Any | None:
        """Call the LLM, parse response into a GoldenMatchConfig.
        Returns None when the LLM declines to propose changes."""
        import json
        import os

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        # Lazy import to keep openai an optional dep
        try:
            from openai import OpenAI
        except ImportError:
            return None

        client = OpenAI(api_key=api_key)
        prompt = self._build_prompt(profile, current, history)

        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2000,
        )
        text = response.choices[0].message.content
        if text is None:
            return None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None

        action = payload.get("action")
        if action == "satisfied":
            return None
        if action != "modify":
            return None

        # Apply the proposed diff to current config
        return self._apply_diff(current, payload.get("diff", {}))

    def _build_prompt(
        self,
        profile: ComplexityProfile,
        current: Any,
        history: RunHistory,
    ) -> str:
        """Render the profile + current config + history as a structured
        prompt the LLM can reason about."""
        # Profile summary
        bp = profile.blocking
        sp = profile.scoring
        cp = profile.cluster
        dp = profile.data

        # Decisions so far
        decisions = "\n".join(
            f"  - {d.rule_name}: {d.rationale}" for d in history.decisions
        ) or "  (no rules fired yet)"

        # Current config
        from goldenmatch.config.schemas import GoldenMatchConfig
        if isinstance(current, GoldenMatchConfig):
            current_json = current.model_dump_json(indent=2)
        else:
            current_json = str(current)

        return f"""\
You are an expert in entity-resolution configuration. The auto-config
controller has run {history.iteration} iteration(s) and the heuristic rule
table can't propose any further improvements, but the current profile is
{profile.health().value.upper()} (not green). Inspect the signals below
and propose a config diff that would address the most likely problem.

## Profile signals

DataProfile: n_rows={dp.n_rows} n_cols={dp.n_cols} types={dict(dp.column_types)}
  cardinality_ratio={dict(dp.cardinality_ratio)}
  null_rate={dict(dp.null_rate)}

BlockingProfile: keys={bp.keys_used} n_blocks={bp.n_blocks}
  reduction_ratio={bp.reduction_ratio} block_sizes_p99={bp.block_sizes_p99}
  singleton_block_count={bp.singleton_block_count}

ScoringProfile: candidates_compared={sp.candidates_compared}
  n_pairs_above_threshold={sp.n_pairs_scored}
  mass_above_threshold={sp.mass_above_threshold}
  mass_in_borderline={sp.mass_in_borderline}
  dip_statistic={sp.dip_statistic}
  random_pair_above_threshold_rate={sp.random_pair_above_threshold_rate}

ClusterProfile: n_clusters={cp.n_clusters} max_size={cp.cluster_size_max}
  transitivity_rate={cp.transitivity_rate}

## Decisions so far

{decisions}

## Current config

{current_json}

## Task

Return a JSON object with one of two actions:

  {{"action": "satisfied"}}
    -- the current config is acceptable; no change needed.

  {{"action": "modify", "diff": {{...}}}}
    -- propose a diff. The diff is a partial GoldenMatchConfig with only
    the fields you want to change. Keep changes minimal and focused on
    the most likely cause of the non-green status.

Examples of useful diffs:

  Lower threshold:
    {{"action": "modify",
      "diff": {{"matchkeys": [{{"name": "...", "threshold": 0.6}}]}}}}

  Switch blocking key:
    {{"action": "modify",
      "diff": {{"blocking": {{"keys": [{{"fields": ["surname"],
                                          "transforms": ["soundex"]}}]}}}}}}

  Drop a problematic matchkey by name:
    {{"action": "modify",
      "diff": {{"drop_matchkeys": ["domain_exact_title_key"]}}}}

Your response MUST be valid JSON, nothing else.
"""

    def _apply_diff(self, current: Any, diff: dict) -> Any | None:
        """Apply a JSON diff to the current GoldenMatchConfig.

        Supports a focused subset: matchkey threshold change, blocking key
        replacement, matchkey drop-by-name. Returns None if the diff doesn't
        produce a valid config or doesn't actually change anything.
        """
        from goldenmatch.config.schemas import GoldenMatchConfig
        if not isinstance(current, GoldenMatchConfig):
            return None

        new_cfg = current

        # Drop matchkeys by name
        if "drop_matchkeys" in diff:
            drop = set(diff["drop_matchkeys"] or [])
            kept = [mk for mk in (new_cfg.matchkeys or []) if mk.name not in drop]
            if len(kept) != len(new_cfg.matchkeys or []):
                new_cfg = new_cfg.model_copy(update={"matchkeys": kept})

        # Threshold tweaks: a list of {name, threshold} entries
        if "matchkeys" in diff and diff["matchkeys"]:
            new_mks = []
            for mk in (new_cfg.matchkeys or []):
                update_for_this = next(
                    (m for m in diff["matchkeys"] if m.get("name") == mk.name),
                    None,
                )
                if update_for_this is not None and "threshold" in update_for_this:
                    new_mks.append(mk.model_copy(update={
                        "threshold": update_for_this["threshold"],
                    }))
                else:
                    new_mks.append(mk)
            new_cfg = new_cfg.model_copy(update={"matchkeys": new_mks})

        # Blocking key swap
        if "blocking" in diff and diff["blocking"] and "keys" in diff["blocking"]:
            from goldenmatch.config.schemas import BlockingKeyConfig
            new_keys = [
                BlockingKeyConfig(
                    fields=k["fields"],
                    transforms=k.get("transforms", ["lowercase"]),
                )
                for k in diff["blocking"]["keys"]
            ]
            if new_cfg.blocking is not None:
                new_cfg = new_cfg.model_copy(update={
                    "blocking": new_cfg.blocking.model_copy(update={"keys": new_keys}),
                })

        if new_cfg == current:
            return None
        return new_cfg


_SYSTEM_PROMPT = (
    "You are an expert in entity resolution. You inspect runtime profile "
    "signals from a deduplication pipeline and propose minimal config diffs "
    "that improve precision/recall without overfitting to the specific data. "
    "Always respond with valid JSON matching the schema described in the user "
    "message."
)
