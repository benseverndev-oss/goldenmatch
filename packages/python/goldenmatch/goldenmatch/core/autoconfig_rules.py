"""Default rule table for HeuristicRefitPolicy.

Each rule is a pure function:
    (ComplexityProfile, GoldenMatchConfig, RunHistory)
        → tuple[GoldenMatchConfig, PolicyDecision] | None

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      §HeuristicRefitPolicy rule table (v1).
"""
from __future__ import annotations
from typing import Any
from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
    BlockingConfig, BlockingKeyConfig,
)
from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.autoconfig_history import RunHistory, PolicyDecision


def _first_weighted_mk(cfg: GoldenMatchConfig) -> MatchkeyConfig | None:
    for mk in cfg.matchkeys or []:
        if mk.type == "weighted":
            return mk
    return None


def _existing_blocking_fields(cfg: GoldenMatchConfig) -> set[str]:
    if cfg.blocking is None:
        return set()
    fields: set[str] = set()
    for k in (cfg.blocking.keys or []):
        for f in (k.fields or []):
            fields.add(f)
    for k in (cfg.blocking.passes or []):
        for f in (k.fields or []):
            fields.add(f)
    return fields


def rule_blocking_singleton_trap(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    """Fires when blocking produces mostly singleton blocks AND the fuzzy
    scorer found no pairs to score. The current blocking key is too
    discriminating — switches to ``first_token`` on the dominant text
    field of the first weighted matchkey.

    Diagnosed from DBLP-ACM: ``__title_key__`` blocking collapses every
    distinct-prefix title to its own block, leaving the fuzzy scorer with
    nothing to compare. ``first_token`` on raw ``title`` puts records
    sharing the first word in the same block, giving the fuzzy scorer
    real pairs to discriminate.
    """
    bp = profile.blocking
    sp = profile.scoring
    if sp.n_pairs_scored != 0 or sp.mass_above_threshold != 0.0:
        return None
    if bp.n_blocks == 0:
        return None
    if (bp.singleton_block_count / bp.n_blocks) <= 0.5:
        return None

    if current.blocking is None:
        return None

    # Target field: first text field in the first weighted matchkey
    mk = _first_weighted_mk(current)
    if mk is None:
        return None
    target_field = None
    for f in mk.fields or []:
        col_type = profile.data.column_types.get(f.field, "unknown")
        if col_type in ("text", "name"):
            target_field = f.field
            break
    if target_field is None:
        return None

    # Build a new blocking key on raw text with first_token + lowercase.
    new_blocking = current.blocking.model_copy(update={
        "strategy": "static",
        "keys": [BlockingKeyConfig(
            fields=[target_field],
            transforms=["lowercase", "first_token"],
        )],
    })
    new_cfg = current.model_copy(update={"blocking": new_blocking})
    decision = PolicyDecision(
        rule_name="blocking_singleton_trap",
        rationale=(
            f"singletons={bp.singleton_block_count}/{bp.n_blocks} "
            f"({bp.singleton_block_count / bp.n_blocks:.0%}); "
            f"n_pairs_scored=0 → switching blocking to "
            f"first_token({target_field!r})"
        ),
        config_diff={
            "blocking.keys[0].fields": [target_field],
            "blocking.keys[0].transforms": ["lowercase", "first_token"],
        },
    )
    return new_cfg, decision


def rule_blocking_too_coarse(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    bp = profile.blocking
    n_rows = profile.data.n_rows
    if bp.n_blocks == 0 or n_rows == 0:
        return None
    avg = n_rows / bp.n_blocks
    if bp.block_sizes_p99 <= 10 * avg:
        return None
    if current.blocking is None:
        return None
    used = _existing_blocking_fields(current)
    candidates = [
        col for col, ratio in profile.data.cardinality_ratio.items()
        if 0.01 <= ratio <= 0.95 and col not in used
    ]
    if not candidates:
        return None
    new_col = candidates[0]
    new_blocking = current.blocking.model_copy(update={
        "keys": [BlockingKeyConfig(fields=[new_col], transforms=["lowercase"])],
    })
    new_cfg = current.model_copy(update={"blocking": new_blocking})
    decision = PolicyDecision(
        rule_name="blocking_too_coarse",
        rationale=f"block_sizes_p99={bp.block_sizes_p99} > 10 * avg ({10*avg:.0f}); "
                  f"trying more selective key '{new_col}' (cardinality "
                  f"{profile.data.cardinality_ratio[new_col]:.2f})",
        config_diff={"blocking.keys[0].fields": [new_col]},
    )
    return new_cfg, decision


def rule_unimodal_scoring(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    sp = profile.scoring
    if sp.dip_statistic >= 0.01 or sp.n_pairs_scored == 0:
        return None
    mk = _first_weighted_mk(current)
    if mk is None:
        return None
    # Pick highest-cardinality matchkey field that exists in the matchkey
    field_names = {f.field for f in mk.fields or []}
    sorted_fields = sorted(
        ((name, fs.post_transform_cardinality_ratio)
         for name, fs in profile.matchkey.per_field.items()
         if name in field_names),
        key=lambda kv: -kv[1],
    )
    if not sorted_fields:
        return None
    target_field = sorted_fields[0][0]
    new_fields = []
    changed = False
    for f in mk.fields:
        if f.field == target_field and f.scorer != "ensemble":
            new_fields.append(f.model_copy(update={"scorer": "ensemble"}))
            changed = True
        else:
            new_fields.append(f)
    if not changed:
        return None
    new_mk = mk.model_copy(update={"fields": new_fields})
    new_matchkeys = [new_mk if m is mk else m for m in current.matchkeys]
    new_cfg = current.model_copy(update={"matchkeys": new_matchkeys})
    decision = PolicyDecision(
        rule_name="unimodal_scoring",
        rationale=f"dip_statistic={sp.dip_statistic:.4f} < 0.01; "
                  f"swapping scorer on '{target_field}' to ensemble",
        config_diff={f"matchkeys[].fields[{target_field}].scorer": "ensemble"},
    )
    return new_cfg, decision


def rule_low_reduction_ratio(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    bp = profile.blocking
    if bp.reduction_ratio >= 0.5:
        return None
    if current.blocking is None or not current.blocking.keys:
        return None
    text_cols = [
        col for col, ctype in profile.data.column_types.items()
        if ctype in ("text", "name")
    ]
    if not text_cols:
        return None
    used = _existing_blocking_fields(current)
    soundex_candidate = next((c for c in text_cols if c not in used), text_cols[0])
    existing_keys = list(current.blocking.keys)
    new_pass = BlockingKeyConfig(fields=[soundex_candidate], transforms=["soundex"])
    new_blocking = current.blocking.model_copy(update={
        "strategy": "multi_pass",
        "passes": existing_keys + [new_pass],
    })
    new_cfg = current.model_copy(update={"blocking": new_blocking})
    decision = PolicyDecision(
        rule_name="low_reduction_ratio",
        rationale=f"reduction_ratio={bp.reduction_ratio:.2f} < 0.5; "
                  f"adding soundex pass on '{soundex_candidate}'",
        config_diff={"blocking.strategy": "multi_pass"},
    )
    return new_cfg, decision


def rule_low_transitivity(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    cp = profile.cluster
    if cp.transitivity_rate >= 0.85 or cp.n_clusters == 0:
        return None
    mk = _first_weighted_mk(current)
    if mk is None or mk.threshold is None:
        return None
    new_threshold = max(0.5, mk.threshold - 0.05)
    if new_threshold == mk.threshold:
        return None
    new_mk = mk.model_copy(update={"threshold": new_threshold})
    new_matchkeys = [new_mk if m is mk else m for m in current.matchkeys]
    new_cfg = current.model_copy(update={"matchkeys": new_matchkeys})
    decision = PolicyDecision(
        rule_name="low_transitivity",
        rationale=f"transitivity={cp.transitivity_rate:.2f} < 0.85; "
                  f"lowering threshold {mk.threshold:.2f} → {new_threshold:.2f}",
        config_diff={"matchkeys[0].threshold": new_threshold},
    )
    return new_cfg, decision


def rule_no_matches(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    sp = profile.scoring
    # Fires on either (a) scored pairs but none above threshold,
    # or (b) blocking produced no scorable pairs at all (singleton trap).
    if sp.mass_above_threshold > 0.0:
        return None  # something matched; not our case
    mk = _first_weighted_mk(current)
    if mk is None:
        return None
    needs_threshold_change = mk.threshold is not None and mk.threshold > 0.5
    needs_blocking_loosen = (
        current.blocking is not None
        and current.blocking.max_block_size < 50000
    )
    if not (needs_threshold_change or needs_blocking_loosen):
        return None
    updates: dict[str, Any] = {}
    if needs_threshold_change:
        new_mk = mk.model_copy(update={"threshold": 0.5})
        updates["matchkeys"] = [new_mk if m is mk else m for m in current.matchkeys]
    if needs_blocking_loosen:
        new_blocking = current.blocking.model_copy(update={
            "max_block_size": 50000,
            "skip_oversized": False,
        })
        updates["blocking"] = new_blocking
    new_cfg = current.model_copy(update=updates)
    decision = PolicyDecision(
        rule_name="no_matches",
        rationale=(
            f"mass_above_threshold={sp.mass_above_threshold} on "
            f"{sp.n_pairs_scored} pairs scored; resetting to permissive baseline "
            f"(lower threshold, broader blocking)"
        ),
        config_diff=updates,
    )
    return new_cfg, decision


DEFAULT_RULES = [
    rule_blocking_singleton_trap,   # NEW: catches __title_key__-style traps before blocking_too_coarse
    rule_blocking_too_coarse,
    rule_unimodal_scoring,
    rule_low_reduction_ratio,
    rule_low_transitivity,
    rule_no_matches,
]
