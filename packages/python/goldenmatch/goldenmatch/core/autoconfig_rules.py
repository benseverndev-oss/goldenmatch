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


def _is_derived(field_name: str) -> bool:
    """Return True if field_name is an auto-generated derived column (prefixed with __)."""
    return field_name.startswith("__")


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
    """Fires when blocking produced blocks but the scorer saw zero candidate
    pairs to compare.  This covers two related pathologies:

    1. Classic singleton trap: every block has exactly one record, so there
       are no within-block pairs to compare.
    2. Cross-source isolation (match mode): blocks were formed but no block
       contains records from both target and reference, so the scorer again
       sees zero candidates.

    The primary signal is ``candidates_compared == 0`` with ``n_blocks > 0``.
    The old ``singleton_block_count / n_blocks > 0.5`` guard is intentionally
    dropped — DBLP-ACM has very few singletons yet still falls into the trap.

    Action: switch to ``first_token`` on the dominant text field of the
    first weighted matchkey, producing coarser blocks that are more likely
    to contain matching cross-source pairs.
    """
    bp = profile.blocking
    sp = profile.scoring
    # If candidates were actually compared, this is not the singleton trap.
    if sp.candidates_compared > 0:
        return None
    if bp.n_blocks == 0:
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
            f"candidates_compared=0 with n_blocks={bp.n_blocks}; "
            f"singletons={bp.singleton_block_count}/{bp.n_blocks} "
            f"({bp.singleton_block_count / bp.n_blocks:.0%}); "
            f"switching blocking to first_token({target_field!r})"
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
    # Only fires when the fuzzy scorer actually compared candidates but none
    # reached the threshold.  When candidates_compared == 0, the singleton-trap
    # rule should fire instead (blocking never produced comparable pairs).
    if sp.candidates_compared == 0:
        return None  # singleton trap territory; let rule_blocking_singleton_trap handle it
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
            f"candidates_compared={sp.candidates_compared}, "
            f"mass_above_threshold={sp.mass_above_threshold}; "
            f"resetting to permissive baseline (lower threshold, broader blocking)"
        ),
        config_diff=updates,
    )
    return new_cfg, decision


def rule_blocking_key_swap(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    """Fires when a prior iteration already loosened the threshold/block cap
    but candidates still aren't matching. The blocking *key* is wrong —
    swap to ``first_token`` on the dominant text field of the first weighted
    matchkey.

    Diagnosed from DBLP-ACM: ``__title_key__`` blocking groups records whose
    full titles share too little for token_sort to score above 0.5 even at
    threshold 0.5. ``first_token`` on raw ``title`` puts records sharing
    the first word in the same block, giving the fuzzy scorer pairs whose
    titles are textually similar enough to actually match.
    """
    sp = profile.scoring
    # Only fire when fuzzy actually compared candidates AND nothing matched
    if sp.candidates_compared == 0:
        return None
    if sp.mass_above_threshold > 0.0:
        return None
    # Only fire after a prior iteration already tried something else (avoid
    # double-firing with rule_no_matches on iter 0; this is the iter-1+ fallback)
    if not history.decisions:
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

    # Avoid proposing a config we already have (anti-oscillation belt-and-suspenders)
    existing_first_key = (current.blocking.keys or [None])[0]
    if (existing_first_key is not None
            and existing_first_key.fields == [target_field]
            and "first_token" in (existing_first_key.transforms or [])):
        return None

    new_blocking = current.blocking.model_copy(update={
        "strategy": "static",
        "keys": [BlockingKeyConfig(
            fields=[target_field],
            transforms=["lowercase", "first_token"],
        )],
    })

    # Drop exact matchkeys whose fields are ENTIRELY derived (__*).
    # These were emitted by domain-extraction paired with the original
    # blocking; once we override the blocking, they're stale.
    surviving_matchkeys = []
    dropped_names = []
    for mk in (current.matchkeys or []):
        if mk.type == "exact" and mk.fields:
            field_names = [f.field for f in mk.fields]
            if all(_is_derived(n) for n in field_names):
                dropped_names.append(mk.name)
                continue
        surviving_matchkeys.append(mk)

    updates: dict[str, Any] = {"blocking": new_blocking}
    if dropped_names:
        updates["matchkeys"] = surviving_matchkeys

    new_cfg = current.model_copy(update=updates)

    rationale_parts = [
        f"after {len(history.decisions)} prior decision(s), "
        f"candidates_compared={sp.candidates_compared} "
        f"but mass_above_threshold={sp.mass_above_threshold}; "
        f"swapping blocking to first_token({target_field!r})"
    ]
    if dropped_names:
        rationale_parts.append(
            f"; dropped {len(dropped_names)} stale derived-column exact "
            f"matchkey(s): {dropped_names}"
        )
    decision = PolicyDecision(
        rule_name="blocking_key_swap",
        rationale="".join(rationale_parts),
        config_diff={
            "blocking.keys[0].fields": [target_field],
            "blocking.keys[0].transforms": ["lowercase", "first_token"],
            **({"matchkeys.dropped": dropped_names} if dropped_names else {}),
        },
    )
    return new_cfg, decision


def rule_blocking_field_null_heavy(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    """Fires when blocking on a single field whose null_rate > 0.10.
    Records with null/missing blocking values can't appear in any block,
    capping recall structurally.

    Action: convert to multi-pass with a second key on a low-null
    high-cardinality field.
    """
    if current.blocking is None or not current.blocking.keys:
        return None
    # Only fire on single-pass, single-key blocking
    if (current.blocking.strategy == "multi_pass"
            and len(current.blocking.passes or []) > 1):
        return None
    if len(current.blocking.keys) > 1:
        return None
    primary_key = current.blocking.keys[0]
    if not primary_key.fields:
        return None
    primary_field = primary_key.fields[0]

    blocking_null_rate = profile.data.null_rate.get(primary_field, 0.0)
    if blocking_null_rate <= 0.10:
        return None

    # Find a low-null high-cardinality alternate
    used = _existing_blocking_fields(current)
    candidates = [
        (col, profile.data.cardinality_ratio.get(col, 0.0))
        for col in profile.data.cardinality_ratio
        if col not in used
        and profile.data.null_rate.get(col, 1.0) < 0.05
        and 0.05 <= profile.data.cardinality_ratio.get(col, 0.0) <= 0.95
    ]
    candidates.sort(key=lambda kv: -kv[1])
    if not candidates:
        return None
    second_field = candidates[0][0]

    existing_keys = list(current.blocking.keys or [])
    new_pass = BlockingKeyConfig(fields=[second_field], transforms=["lowercase"])
    new_blocking = current.blocking.model_copy(update={
        "strategy": "multi_pass",
        "passes": existing_keys + [new_pass],
    })
    new_cfg = current.model_copy(update={"blocking": new_blocking})
    decision = PolicyDecision(
        rule_name="blocking_field_null_heavy",
        rationale=(
            f"blocking field {primary_field!r} has null_rate="
            f"{blocking_null_rate:.2f} > 0.10; adding multi-pass on "
            f"low-null alternate {second_field!r}"
        ),
        config_diff={
            "blocking.strategy": "multi_pass",
            "blocking.passes[+]": [second_field],
        },
    )
    return new_cfg, decision


def rule_recall_gap_suspected(
    profile: ComplexityProfile, current: GoldenMatchConfig, history: RunHistory
) -> "tuple[GoldenMatchConfig, PolicyDecision] | None":
    """Fires when the random-pair probe finds a non-trivial fraction of
    non-blocked pairs that score above threshold — signal that blocking is
    excluding real matches.

    Also fires on a secondary signal: when blocking is over-tight (very high
    reduction ratio, perfect or near-perfect mass_above_threshold, and very
    few candidates relative to the sample size). This catches the pathology
    where blocking on an identifier column (e.g. soc_sec_id) is so restrictive
    that it misses true matches with different/corrupted ID values, even though
    the scored candidates all match (mass == 1.0).

    Action: convert single-pass blocking to multi-pass by adding an orthogonal
    second pass on the highest-cardinality non-null user column not already
    in blocking.
    """
    sp = profile.scoring
    if current.blocking is None:
        return None
    # Already multi-pass with >1 pass — don't re-fire
    if (current.blocking.strategy == "multi_pass"
            and len(current.blocking.passes or []) > 1):
        return None

    # Signal 1: random-pair probe rate exceeds threshold
    probe_fires = (
        sp.random_pair_above_threshold_rate is not None
        and sp.random_pair_above_threshold_rate >= 0.05
    )

    # Signal 2: over-tight blocking — perfect precision with very few candidates.
    # mass_above_threshold == 1.0 means every scored pair matched (no false candidates),
    # combined with candidates_compared being tiny relative to sample size and
    # a very high reduction ratio — hallmark of an over-restrictive identifier key.
    n_rows = profile.data.n_rows
    tight_blocking_fires = (
        sp.mass_above_threshold >= 1.0
        and sp.candidates_compared > 0
        and n_rows > 0
        and sp.candidates_compared < n_rows * 0.5
        and profile.blocking.reduction_ratio > 0.995
    )

    if not probe_fires and not tight_blocking_fires:
        return None

    used = _existing_blocking_fields(current)
    # Pick the highest-cardinality column not already used and with low null rate.
    # Prefer name-type columns for the tight-blocking case (add a phonetic pass).
    name_type_cols = [
        col for col in profile.data.cardinality_ratio
        if col not in used
        and profile.data.null_rate.get(col, 1.0) < 0.20
        and 0.05 <= profile.data.cardinality_ratio.get(col, 0.0) <= 0.95
        and profile.data.column_types.get(col) in ("name", "text")
    ]
    all_candidates = [
        (col, profile.data.cardinality_ratio.get(col, 0.0))
        for col in profile.data.cardinality_ratio
        if col not in used
        and profile.data.null_rate.get(col, 1.0) < 0.20
        and 0.05 <= profile.data.cardinality_ratio.get(col, 0.0) <= 0.95
    ]
    all_candidates.sort(key=lambda kv: -kv[1])
    if not all_candidates:
        return None

    # For the tight-blocking case, prefer a name-type column so we get a
    # phonetic blocking pass that covers records with variant spellings.
    if tight_blocking_fires and name_type_cols:
        # Pick the name column with the highest cardinality
        second_field = sorted(
            name_type_cols,
            key=lambda c: -profile.data.cardinality_ratio.get(c, 0.0),
        )[0]
        transforms = ["soundex"]  # soundex for phonetic recall
    else:
        second_field = all_candidates[0][0]
        transforms = ["lowercase"]

    existing_keys = list(current.blocking.keys or [])
    new_pass = BlockingKeyConfig(fields=[second_field], transforms=transforms)
    new_blocking = current.blocking.model_copy(update={
        "strategy": "multi_pass",
        "passes": existing_keys + [new_pass],
    })
    new_cfg = current.model_copy(update={"blocking": new_blocking})

    if probe_fires:
        rationale = (
            f"random_pair_above_threshold_rate={sp.random_pair_above_threshold_rate:.3f} "
            f"> 0.05 — blocking may exclude real matches; "
            f"adding multi-pass on {second_field!r}"
        )
    else:
        rationale = (
            f"mass_above_threshold={sp.mass_above_threshold:.3f}==1.0 with "
            f"candidates_compared={sp.candidates_compared} < n_rows*0.5 ({n_rows * 0.5:.0f}) "
            f"and rr={profile.blocking.reduction_ratio:.4f}>0.995 — "
            f"blocking too tight (identifier key may miss perturbed values); "
            f"adding multi-pass on {second_field!r}"
        )

    decision = PolicyDecision(
        rule_name="recall_gap_suspected",
        rationale=rationale,
        config_diff={
            "blocking.strategy": "multi_pass",
            "blocking.passes[+]": [second_field],
        },
    )
    return new_cfg, decision


DEFAULT_RULES = [
    rule_blocking_field_null_heavy,   # NEW: structural null-rate guard (runs first)
    rule_blocking_singleton_trap,     # catches __title_key__-style traps before blocking_too_coarse
    rule_blocking_too_coarse,
    rule_unimodal_scoring,
    rule_low_reduction_ratio,
    rule_low_transitivity,
    rule_no_matches,
    rule_blocking_key_swap,           # iter-1+ fallback when threshold/block loosening didn't help
    rule_recall_gap_suspected,        # NEW: probe-based recall signal (runs last)
]
